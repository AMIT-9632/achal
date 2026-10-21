# SPDX-License-Identifier: MIT
"""Steps every node of a site through a scenario and yields labelled telemetry dicts."""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from achal_common.config import SiteConfig, load_site

from achal_sim.geomech import Response
from achal_sim.scenarios import FIRE_HAZARD_DT_C, ScenarioPlan, build_plan
from achal_sim.sensors import Environment, NodeSensors

DEFAULT_START = datetime(2026, 10, 1, tzinfo=UTC)
IST = timedelta(hours=5, minutes=30)

# Dhanbad, late October: about 20 degC at dawn, 31 degC mid-afternoon.
TEMP_MEAN_C, TEMP_AMP_C, TEMP_PEAK_IST_H = 25.5, 5.5, 14.5
SUNRISE_IST_H, DAY_LENGTH_H = 6.0, 11.5

# Vibration sources, g at the source and attenuation length in m.
MICROSEISMIC_BASE_PER_H = 0.5   # creep cracking near a failing pillar, rises as 1/(t_f - t)
MICROSEISMIC_MAX_PER_H = 120.0
MICROSEISMIC_G, MICROSEISMIC_ATTEN_M = 0.004, 15.0
COLLAPSE_G, COLLAPSE_ATTEN_M = 0.3, 20.0
AFTERSHOCK_PER_H, AFTERSHOCK_DECAY_H, AFTERSHOCK_SPAN_H = 60.0, 0.5, 12.0
BLAST_DAY_PROB, BLAST_G = 0.7, 0.02  # nearby opencast benches blast around midday


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rounded(fields: dict) -> dict:
    digits = {"tilt_x_deg": 5, "tilt_y_deg": 5, "crack_mm": 3, "ext_mm": 3, "vib_rms_g": 5, "vib_peak_g": 5,
              "temp_c": 2, "batt_v": 3}
    return {k: round(v, digits[k]) for k, v in fields.items()}


class FieldSimulator:
    def __init__(self, site: SiteConfig, scenario: str = "normal", seed: int = 42, start: datetime | None = None,
                 period_s: float | None = None, duration_h: float | None = None) -> None:
        self.site = site
        self.seed = seed
        self.plan: ScenarioPlan = build_plan(site, scenario, seed, duration_h)
        self.start = start or DEFAULT_START
        self.period_s = float(period_s or site.mesh.sample_period_s)
        self.seq = 0
        self.rng = random.Random(f"{seed}:{scenario}:site")
        self.nodes = site.node_ids()
        self.pos = {n: site.node_position_m(n) for n in self.nodes}
        self.sensors = {n: NodeSensors(n, seed, self.plan.faults.get(n)) for n in self.nodes}
        self.ext_pair = {n: self._ext_neighbour(n) for n in self.nodes}
        # Unit-amplitude response of every bowl at every node, and of horizontal movement at the
        # extensometer anchor, computed once: each sample only rescales them.
        self.unit: list[dict[str, Response]] = []
        self.unit_anchor: list[dict[str, Response]] = []
        for comp in self.plan.components:
            self.unit.append({n: comp.bowl.response(self.pos[n].x, self.pos[n].y) for n in self.nodes})
            self.unit_anchor.append({n: comp.bowl.response(*self.ext_pair[n][0]) for n in self.nodes})
        self._days: dict[int, tuple[float, float, float | None]] = {}

    @property
    def scenario(self) -> str:
        return self.plan.name

    def _ext_neighbour(self, node: str) -> tuple[tuple[float, float], tuple[float, float]]:
        """Each node's extensometer wire runs to the next node east (west for the last column)."""
        i = int(node[1:])
        cols = self.site.mesh.grid.cols
        j = i + 1 if (i % cols) < cols - 1 else i - 1
        if cols == 1:
            j = i + cols
        other = self.pos[f"n{j:02d}"] if f"n{j:02d}" in self.pos else self.pos[node]
        here = self.pos[node]
        length = math.hypot(other.x - here.x, other.y - here.y) or 1.0
        return (other.x, other.y), ((other.x - here.x) / length, (other.y - here.y) / length)

    # --- site-wide conditions ---

    def _day(self, day: int) -> tuple[float, float, float | None]:
        """(temperature offset, cloud factor, blast hour IST or None) for a local calendar day (ordinal)."""
        if day not in self._days:
            r = random.Random(f"{self.seed}:{day}:weather")
            blast = r.uniform(12.5, 14.5) if r.random() < BLAST_DAY_PROB else None
            self._days[day] = (r.gauss(0, 1.2), r.uniform(0.55, 1.0), blast)
        return self._days[day]

    def _environment(self, ts: datetime, t_h: float) -> tuple[Environment, float | None]:
        local = ts + IST
        hour = local.hour + local.minute / 60 + local.second / 3600
        _, cloud, blast_h = self._day(local.toordinal())
        # Day-to-day weather offsets are anchored at local noon and blended, so there is no midnight step.
        noon_days = local.toordinal() + (hour - 12) / 24
        d0 = math.floor(noon_days)
        w = noon_days - d0
        temp_offset = (1 - w) * self._day(d0)[0] + w * self._day(d0 + 1)[0]
        air = TEMP_MEAN_C + temp_offset + TEMP_AMP_C * math.cos(2 * math.pi * (hour - TEMP_PEAK_IST_H) / 24)
        daylight = (hour - SUNRISE_IST_H) / DAY_LENGTH_H
        solar = cloud * math.sin(math.pi * daylight) if 0 < daylight < 1 else 0.0
        dt_h = self.period_s / 3600
        blast_now = blast_h is not None and hour - dt_h < blast_h <= hour
        return Environment(t_h=t_h, dt_h=dt_h, air_temp_c=air, solar=solar), blast_h if blast_now else None

    def _shocks(self, t_h: float, dt_h: float, blast: bool) -> list[tuple[float | None, float, float, float, float]]:
        """Vibration sources in the window (t - dt, t]: (x, y, amplitude g, duration s, attenuation m).

        x = None means a distant source felt about equally by every node (blasting).
        """
        r = self.rng
        shocks = []
        for ph in self.plan.potholes:
            if ph.onset_h <= t_h < ph.failure_h:
                rate = min(MICROSEISMIC_MAX_PER_H,
                           MICROSEISMIC_BASE_PER_H * (ph.failure_h - ph.onset_h) / (ph.failure_h - t_h))
                progress = (t_h - ph.onset_h) / (ph.failure_h - ph.onset_h)
                for _ in range(_poisson(r, rate * dt_h)):
                    amp = MICROSEISMIC_G * (1 + 2 * progress) * r.lognormvariate(0, 0.5)
                    shocks.append((ph.cx, ph.cy, amp, r.uniform(0.5, 2.0), MICROSEISMIC_ATTEN_M))
            if t_h - dt_h < ph.failure_h <= t_h:
                shocks.append((ph.cx, ph.cy, COLLAPSE_G * r.uniform(0.7, 1.3), 30.0, COLLAPSE_ATTEN_M))
            if ph.failure_h < t_h <= ph.failure_h + AFTERSHOCK_SPAN_H:
                rate = AFTERSHOCK_PER_H / (1 + (t_h - ph.failure_h) / AFTERSHOCK_DECAY_H)
                for _ in range(_poisson(r, rate * dt_h)):
                    amp = 0.02 * r.lognormvariate(0, 0.6)
                    shocks.append((ph.cx, ph.cy, amp, r.uniform(0.5, 3.0), COLLAPSE_ATTEN_M))
        if blast:
            shocks.append((None, 0.0, BLAST_G * r.uniform(0.6, 1.4), 3.0, 0.0))
        return shocks

    def _vibration(self, node: str, shocks: list, period_s: float) -> tuple[float, float]:
        """Window RMS and peak at a node from the sources (short events add energy for their duration)."""
        energy, peak = 0.0, 0.0
        p = self.pos[node]
        for x, y, amp, dur, atten in shocks:
            a = amp * self.sensors[node].rng.uniform(0.8, 1.2) if x is None else \
                amp * math.exp(-math.hypot(p.x - x, p.y - y) / atten)
            energy += a**2 * min(1.0, dur / period_s) / 2  # sinusoidal burst: rms^2 = a^2 / 2
            peak = max(peak, a)
        return math.sqrt(energy), peak

    # --- per-node state ---

    def ground(self, node: str, t_h: float) -> tuple[Response, float]:
        """True ground response at a node and the true extensometer stretch to its neighbour (mm)."""
        total = Response()
        ext = 0.0
        (_, (ex, ey)) = self.ext_pair[node]
        for comp, unit, anchor in zip(self.plan.components, self.unit, self.unit_anchor, strict=True):
            k = comp.time(t_h)
            if k == 0.0:
                continue
            here = unit[node]
            total = total + here.scaled(k)
            ext += k * ((anchor[node].u_x - here.u_x) * ex + (anchor[node].u_y - here.u_y) * ey)
        return total, ext

    def truth(self, node: str, t_h: float, s_mm: float, heat_dt: float, blast: bool, ts: datetime) -> dict:
        plan = self.plan
        event = "none"
        if node in plan.pothole_nodes and any(ph.failure_h <= t_h for ph in plan.potholes):
            event = "collapse"
        elif node in plan.pothole_nodes and any(ph.onset_h <= t_h for ph in plan.potholes):
            event = "precursor"
        elif node in plan.trough_nodes and plan.onset_h is not None and t_h >= plan.onset_h:
            event = "trough"
        elif heat_dt >= FIRE_HAZARD_DT_C:
            event = "fire"
        elif blast:
            event = "blast"
        hazard = event in ("collapse", "precursor", "trough", "fire")
        out: dict = {"subsidence_mm": round(s_mm, 3), "event": event, "hazard": hazard}
        fault = self.sensors[node].fault_active(t_h)
        if fault:
            out["fault"] = fault
        if hazard:
            if plan.onset_h is not None:
                out["onset_ts"] = _iso(self.start + timedelta(hours=plan.onset_h))
            if plan.failure_h is not None:
                out["failure_ts"] = _iso(self.start + timedelta(hours=plan.failure_h))
        return out

    def step(self) -> list[dict]:
        """Advance one sample period; return one telemetry dict per transmitting node."""
        self.seq += 1
        seq = self.seq - 1
        t_s = seq * self.period_s
        t_h = t_s / 3600
        ts = self.start + timedelta(seconds=t_s)
        env, blast_h = self._environment(ts, t_h)
        blast = blast_h is not None
        shocks = self._shocks(t_h, env.dt_h, blast)
        out = []
        for node in self.nodes:
            ground, ext = self.ground(node, t_h)
            p = self.pos[node]
            heat = self.plan.heat.delta_t(p.x, p.y, t_h) if self.plan.heat else 0.0
            fields = self.sensors[node].read(env, ground, ext, heat, self._vibration(node, shocks, self.period_s))
            if fields is None:
                continue
            msg = {"v": 1, "site": self.site.site_id, "node": node, "seq": seq, "ts": _iso(ts), **_rounded(fields),
                   "truth": self.truth(node, t_h, ground.s_mm, heat, blast, ts)}
            out.append(msg)
        return out


def _poisson(r: random.Random, lam: float) -> int:
    """Knuth's method; lam is small (< ~5) for one sample window."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= r.random()
        if p <= limit:
            return k
        k += 1


def simulate(scenario: str = "normal", seed: int = 42, hours: float | None = None, start: datetime | None = None,
             every: int = 1, site: SiteConfig | str | None = None, period_s: float | None = None) -> Iterator[dict]:
    """Yield labelled telemetry dicts (JSON-ready) for a whole scenario run.

    hours defaults to the scenario's length; every=N keeps one sample period in N (the
    simulation still steps every period, so physics and noise do not depend on it).
    """
    site_cfg = site if isinstance(site, SiteConfig) else load_site(site)
    sim = FieldSimulator(site_cfg, scenario, seed, start, period_s, hours)
    steps = int(round(sim.plan.duration_h * 3600 / sim.period_s))
    for i in range(steps):
        batch = sim.step()
        if i % every == 0:
            yield from batch
