# SPDX-License-Identifier: MIT
"""Small built-in scenario generator so the engine and KPI harness can be developed
and unit-tested without the full simulator. All output is SYNTHETIC.

It is deliberately simpler than W1's field simulator (services/field-sim), which
is the source for the KPI numbers we report. Physics used:
- Knothe influence-function trough over the panel, S(x, y) via the erf closed form,
  radius of influence R = H * tan(angle of draw), horizontal displacement u = B * grad S
  with B = R / sqrt(2 pi) (Knothe-Budryk), tilt = grad S, strain = div u.
- Pillar-collapse precursor creep d(t) = A ln((tf - t0) / (tf - t)), whose velocity
  A / (tf - t) gives a linear inverse-velocity line (Fukuzono), then a pothole sag.
- Diurnal temperature with per-node thermal coupling, sensor noise, daily opencast
  blasting (vibration only), and sensor faults (stuck, spike, drift, battery).
"""

from __future__ import annotations

import math

import numpy as np

from achal_risk.data import Run
from achal_risk.store import BT, CHANNELS, CR, EX, TP, TX, TY, VB

SCENARIOS = ("normal", "trough", "pillar_collapse", "faults", "fire")
DAYS = {"normal": 30, "trough": 21, "pillar_collapse": 8, "faults": 30, "fire": 14}
START = 1_767_225_600.0  # 2026-01-01T00:00:00Z
_erf = np.vectorize(math.erf)


def _knothe(x, y, x1, x2, y1, y2, r):
    k = math.sqrt(math.pi) / r
    return 0.25 * (_erf(k * (x - x1)) - _erf(k * (x - x2))) * (_erf(k * (y - y1)) - _erf(k * (y - y2)))


def _gauss(x, y, cx, cy, s):
    return np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * s * s))


class _Field:
    """A unit subsidence shape sampled at the nodes: S, tilt, extensometer and crack responses."""

    def __init__(self, fn, xy: np.ndarray, spacing: float, b: float, east: np.ndarray) -> None:
        h = 0.5
        x, y = xy[:, 0], xy[:, 1]
        self.s = fn(x, y)
        gx = (fn(x + h, y) - fn(x - h, y)) / (2 * h)
        gy = (fn(x, y + h) - fn(x, y - h)) / (2 * h)
        self.tilt_x = np.degrees(gx)
        self.tilt_y = np.degrees(gy)

        def ux(px, py):
            return b * (fn(px + h, py) - fn(px - h, py)) / (2 * h)

        def uy(px, py):
            return b * (fn(px, py + h) - fn(px, py - h)) / (2 * h)

        # Extensometer wire to the east neighbour (west for the last column), mm.
        self.ext = (ux(x + east * spacing, y) - ux(x, y)) * east * 1000
        div = (ux(x + h, y) - ux(x - h, y)) / (2 * h) + (uy(x, y + h) - uy(x, y - h)) / (2 * h)
        self.crack = div * 2000  # 2 m crack-gauge base, opening on tension


def generate(site, scenario: str, seed: int, days: float | None = None) -> Run:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario}")
    rng = np.random.default_rng(seed * 101 + SCENARIOS.index(scenario))
    ids = site.node_ids()
    n = len(ids)
    xy = np.array([[p.x, p.y] for p in (site.node_position_m(i) for i in ids)])
    period = site.mesh.sample_period_s
    days = days or DAYS[scenario]
    t = np.arange(int(days * 86400 / period)) * period
    tt = len(t)
    td = t / 86400
    cols = site.mesh.grid.cols
    east = np.where((np.arange(n) % cols) == cols - 1, -1.0, 1.0)
    pnl = site.panel
    r_inf = pnl.seam_depth_m * math.tan(math.radians(pnl.angle_of_draw_deg))
    b = r_inf / math.sqrt(2 * math.pi)

    x = np.zeros((tt, n, len(CHANNELS)))
    # Environment: diurnal temperature, thermal coupling, noise, battery, blasting.
    hod = (td % 1) * 24
    air = 27 + 6 * np.sin(2 * np.pi * (hod - 9) / 24) + rng.normal(0, 0.5) * np.sin(2 * np.pi * td / 9)
    temp = air[:, None] + rng.normal(0, 1.0, n)[None] + rng.normal(0, 0.3, (tt, n))
    k_tilt = rng.normal(0, 0.0012, (2, n))
    k_crack = rng.normal(0, 0.004, n)
    k_ext = rng.normal(0.08, 0.02, n)
    dt_ = temp - temp[: int(3600 / period)].mean(0)
    x[:, :, TX] = rng.normal(0, 0.002, (tt, n)) + k_tilt[0] * dt_
    x[:, :, TY] = rng.normal(0, 0.002, (tt, n)) + k_tilt[1] * dt_
    x[:, :, CR] = rng.normal(0, 0.02, (tt, n)) + k_crack * dt_
    x[:, :, EX] = rng.normal(0, 0.05, (tt, n)) + k_ext * dt_
    x[:, :, VB] = 5e-4 * np.exp(rng.normal(0, 0.35, (tt, n)))
    x[:, :, BT] = 3.95 - 0.002 * td[:, None] + rng.normal(0, 0.005, (tt, n))
    for day in range(int(days) + 1):  # one opencast blast a day, felt by every node
        k = int((day + rng.uniform(11, 15) / 24) * 86400 / period)
        if k < tt:
            amp = rng.uniform(0.01, 0.04) * np.exp(-np.hypot(*(xy - rng.uniform(-200, 300, 2)).T) / 400)
            x[k:k + rng.integers(1, 3), :, VB] += amp

    onset = np.full(n, np.inf)
    failure = None
    fault: list[str | None] = [None] * n
    defo = np.zeros((tt, n, 4))  # tilt_x, tilt_y, crack, ext from ground movement

    def add(field: _Field, f_t: np.ndarray) -> None:
        defo[:, :, 0] += f_t[:, None] * field.tilt_x
        defo[:, :, 1] += f_t[:, None] * field.tilt_y
        defo[:, :, 2] += f_t[:, None] * field.crack
        defo[:, :, 3] += f_t[:, None] * field.ext

    if scenario == "trough":
        smax = 0.5 * pnl.extraction_height_m
        shape = _Field(lambda px, py: _knothe(px, py, 0, pnl.width_m, 0, pnl.length_m, r_inf), xy,
                       site.mesh.spacing_m, b, east)
        t_on = rng.uniform(2, 4)
        mid, w = t_on + rng.uniform(9, 14), rng.uniform(1.5, 2.5)
        f_t = smax / (1 + np.exp(-(td - mid) / w))
        f_t = np.where(td >= t_on, f_t - smax / (1 + math.exp(-(t_on - mid) / w)), 0.0)
        add(shape, f_t)
        onset[:] = START + t_on * 86400
        tilt_mag = np.hypot(defo[:, :, 0], defo[:, :, 1]).max(1)
        hit = np.nonzero(tilt_mag >= math.degrees(1 / 300))[0]
        failure = START + t[hit[0]] if len(hit) else None
    elif scenario == "pillar_collapse":
        c = rng.uniform(20, pnl.width_m - 20, 2)
        t_on = rng.uniform(1.5, 2.5)
        tf = t_on + rng.uniform(2, 5)
        a = rng.uniform(0.015, 0.03)
        creep = _Field(lambda px, py: _gauss(px, py, c[0], c[1], 25.0), xy, site.mesh.spacing_m, b, east)
        sag = _Field(lambda px, py: _gauss(px, py, c[0], c[1], 10.0), xy, site.mesh.spacing_m, b, east)
        left = np.maximum(tf - td, 10 / 1440)
        d = np.where(td >= t_on, a * np.log((tf - t_on) / left), 0.0)
        d = np.where(td >= tf, d.max(), d)
        add(creep, d)
        add(sag, np.where(td >= tf, rng.uniform(0.3, 1.0), 0.0))
        near = creep.s
        hazard = near > 0.2
        onset[hazard] = START + t_on * 86400
        failure = START + tf * 86400
        p = np.where((td >= t_on) & (td < tf), np.minimum(0.4, 0.01 * (tf - t_on) / left), 0.0)
        burst = rng.random((tt, n)) < p[:, None] * near[None]
        x[:, :, VB] += burst * rng.uniform(0.002, 0.01, (tt, n)) * near[None]
        k = int(tf * 86400 / period)
        x[k:k + 2, :, VB] += 0.2 * np.exp(-np.hypot(*(xy - c).T) / 60)
        dead = np.hypot(*(xy - c).T) < 8
        x[k + 1:, dead] = np.nan
    elif scenario == "faults":
        picks = rng.choice(n, 4, replace=False)
        kinds = ["stuck", "spike", "drift", "battery"]
        for i, kind in zip(picks, kinds, strict=True):
            fault[i] = kind
            t_f = rng.uniform(3, 10)
            k = int(t_f * 86400 / period)
            if kind == "stuck":
                x[k:, i, TX] = x[k, i, TX]
            elif kind == "spike":
                hits = rng.random(tt) < 1 / 120
                hits[:k] = False
                x[hits, i, CR] += rng.choice([-1, 1], hits.sum()) * rng.uniform(5, 30, hits.sum())
            elif kind == "drift":
                x[:, i, TY] += np.where(td >= t_f, (td - t_f) * rng.uniform(0.03, 0.08), 0.0)
            else:
                v = np.interp(td, [0, 5, days], [3.95, 3.7, 3.1])
                x[:, i, BT] = v + rng.normal(0, 0.005, tt)
                low = v < 3.3
                x[low, i, :4] += rng.normal(0, 1, (low.sum(), 4)) * np.array([0.008, 0.008, 0.08, 0.2])
    elif scenario == "fire":
        c = rng.uniform(15, pnl.width_m - 15, 2)
        t_on = rng.uniform(1, 3)
        tf = t_on + rng.uniform(6, 10)
        heat = _gauss(xy[:, 0], xy[:, 1], c[0], c[1], 18.0)
        emax = rng.uniform(25, 60)
        e_t = emax / (1 + np.exp(-(td - t_on - 2.5) / 0.8))
        e_t = np.where(td >= t_on, e_t, 0.0)
        x[:, :, TP] = temp + e_t[:, None] * heat[None]
        temp = x[:, :, TP]
        bowl = _Field(lambda px, py: _gauss(px, py, c[0], c[1], 20.0), xy, site.mesh.spacing_m, b, east)
        pot = _Field(lambda px, py: _gauss(px, py, c[0], c[1], 7.0), xy, site.mesh.spacing_m, b, east)
        add(bowl, np.clip((td - t_on) / (tf - t_on), 0, 1) ** 2 * rng.uniform(0.03, 0.08))
        add(pot, np.where(td >= tf, rng.uniform(0.15, 0.4), 0.0))
        hazard = heat > 0.3
        onset[hazard] = START + t_on * 86400
        failure = START + tf * 86400

    x[:, :, TP] = temp
    x[:, :, :4] += defo
    return Run(scenario, seed, "surrogate", START + t, x, ids, onset, failure, fault)
