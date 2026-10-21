# SPDX-License-Identifier: MIT
"""Local rule check on the gateway: sounds sirens with no cloud and no uplink.

This is the fail-safe layer under the AI risk engine, so it is kept simple and
conservative:

1. Each node learns a baseline from its first readings.
2. A node is *alarmed* when the change from baseline in tilt, crack opening or
   extensometer stretch, or the vibration peak, passes a threshold in at least
   2 of its last 3 readings (one spike is not enough: sensor faults spike).
3. The site sounds sirens only when at least `min_nodes` alarmed nodes are
   neighbours of each other within `window_s`. Level is critical if any of them
   is past `critical_factor` x threshold, otherwise warning.
4. Sirens near the alarmed patch go on, and go off after `hold_s` without
   consensus. Times are sensor time (reading `ts`), so accelerated simulation works.

Thresholds are starting values for the synthetic Jharia panel, overridable with
GATEWAY_RULES='{"tilt_deg": 0.1, ...}'. Calibration against simulator runs is
recorded in docs/gateway.md.
"""

from __future__ import annotations

import json
import math
import os
from collections import deque
from dataclasses import dataclass, field, fields
from datetime import datetime

from achal_common import Telemetry
from achal_common.config import SiteConfig


@dataclass
class Thresholds:
    tilt_deg: float = 0.10          # change in tilt magnitude from baseline
    crack_mm: float = 2.0           # crack-gauge opening since baseline
    ext_mm: float = 5.0             # extensometer stretch since baseline
    vib_peak_g: float = 0.05        # absolute vibration peak
    critical_factor: float = 3.0
    window_s: float = 1800          # alarms older than this do not count towards consensus
    neighbour_factor: float = 1.6   # neighbours are within this many grid spacings
    min_nodes: int = 2
    hold_s: float = 3600
    baseline_n: int = 10
    siren_radius_m: float = 400

    @classmethod
    def from_env(cls) -> Thresholds:
        raw = json.loads(os.environ.get("GATEWAY_RULES", "{}") or "{}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class SirenDecision:
    siren_id: str
    on: bool
    level: str | None
    ts: datetime
    nodes: list[str]
    reasons: list[str]


@dataclass
class _NodeState:
    samples: list[Telemetry] = field(default_factory=list)
    base: dict[str, float] | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=3))  # (ratio, reasons) per reading
    alarmed_at: datetime | None = None
    ratio: float = 0.0
    reasons: list[str] = field(default_factory=list)


class LocalRules:
    def __init__(self, site: SiteConfig, thresholds: Thresholds | None = None) -> None:
        self.site = site
        self.th = thresholds or Thresholds.from_env()
        self.pos = {n: site.node_position_m(n) for n in site.node_ids()}
        reach = self.th.neighbour_factor * site.mesh.spacing_m
        self.neighbours = {
            a: {b for b in self.pos if b != a and math.dist((pa.x, pa.y), (self.pos[b].x, self.pos[b].y)) <= reach}
            for a, pa in self.pos.items()}
        self.nodes: dict[str, _NodeState] = {}
        self.siren_level: dict[str, str | None] = {s.id: None for s in site.sirens}
        self.last_consensus: datetime | None = None

    def _evaluate(self, st: _NodeState, r: Telemetry) -> tuple[float, list[str]]:
        b = st.base
        parts = {
            "tilt_rate_high": math.hypot(r.tilt_x_deg - b["tx"], r.tilt_y_deg - b["ty"]) / self.th.tilt_deg,
            "crack_opening": (r.crack_mm - b["crack"]) / self.th.crack_mm,
            "ext_stretch": abs(r.ext_mm - b["ext"]) / self.th.ext_mm,
            "vibration_burst": (r.vib_peak_g if r.vib_peak_g is not None else 3 * r.vib_rms_g) / self.th.vib_peak_g,
        }
        return max(parts.values()), [k for k, v in parts.items() if v >= 1]

    def observe(self, r: Telemetry) -> list[SirenDecision]:
        st = self.nodes.setdefault(r.node, _NodeState())
        if st.base is None:
            st.samples.append(r)
            if len(st.samples) >= self.th.baseline_n:
                s = st.samples
                st.base = {"tx": sum(x.tilt_x_deg for x in s) / len(s), "ty": sum(x.tilt_y_deg for x in s) / len(s),
                           "crack": sum(x.crack_mm for x in s) / len(s), "ext": sum(x.ext_mm for x in s) / len(s)}
                st.samples = []
            return self._sirens(r.ts)
        ratio, reasons = self._evaluate(st, r)
        st.recent.append((ratio, reasons))
        over = [x for x in st.recent if x[0] >= 1]
        if len(over) >= 2:
            st.alarmed_at = r.ts
            st.ratio = min(x[0] for x in over)  # the level both readings support
            st.reasons = sorted({c for x in over for c in x[1]})
        return self._sirens(r.ts)

    def _alarmed(self, now: datetime) -> set[str]:
        return {n for n, st in self.nodes.items()
                if st.alarmed_at and (now - st.alarmed_at).total_seconds() <= self.th.window_s}

    def _sirens(self, now: datetime) -> list[SirenDecision]:
        alarmed = self._alarmed(now)
        agreeing = sorted(n for n in alarmed if self.neighbours[n] & alarmed)
        decisions = []
        if len(agreeing) >= self.th.min_nodes:
            self.last_consensus = now
            critical = any(self.nodes[n].ratio >= self.th.critical_factor for n in agreeing)
            level = "critical" if critical else "warning"
            reasons = sorted({c for n in agreeing for c in self.nodes[n].reasons} | {"neighbour_consensus"})
            for siren_id in self._sirens_near(agreeing):
                if self.siren_level[siren_id] != level and not (
                        self.siren_level[siren_id] == "critical" and level == "warning"):  # never step down while on
                    self.siren_level[siren_id] = level
                    decisions.append(SirenDecision(siren_id, True, level, now, agreeing, reasons))
        elif self.last_consensus and (now - self.last_consensus).total_seconds() > self.th.hold_s:
            for siren_id, lvl in self.siren_level.items():
                if lvl is not None:
                    self.siren_level[siren_id] = None
                    decisions.append(SirenDecision(siren_id, False, None, now, [], []))
            self.last_consensus = None
        return decisions

    def _sirens_near(self, nodes: list[str]) -> list[str]:
        cx = sum(self.pos[n].x for n in nodes) / len(nodes)
        cy = sum(self.pos[n].y for n in nodes) / len(nodes)
        dist = {s.id: math.dist((cx, cy), (s.position_m.x, s.position_m.y)) for s in self.site.sirens}
        near = [s for s, d in dist.items() if d <= self.th.siren_radius_m]
        return near or [min(dist, key=dist.get)]
