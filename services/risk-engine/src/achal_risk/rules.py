# SPDX-License-Identifier: MIT
"""Explainable per-node rules on rates and levels.

Each feature is mapped onto the 0-1 score by piecewise-linear interpolation through
its (watch, warning, critical) thresholds, landing on the level boundaries
0.35 / 0.60 / 0.85. Rates lead: a tilting node is flagged long before its
cumulative tilt reaches the structural damage limit.

Level thresholds for tilt follow the classic angular-distortion damage limits
(1/500 for first cracking in framed buildings, 1/300 for structural damage;
Bjerrum 1963). Rate thresholds are starting values; `calibrate_floor` raises
each watch threshold above the noise seen on normal-scenario data so sensor
noise alone cannot reach Watch. See docs/risk-engine.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from achal_risk.features import Features

BOUNDS = (0.35, 0.60, 0.85)

# feature: (watch, warning, critical, group, reason code)
DEFAULT_THRESHOLDS: dict[str, tuple[float, float, float, str, str]] = {
    "tilt_rate6": (0.02, 0.08, 0.30, "tilt", "tilt_rate_high"),      # deg/day
    "tilt_rate24": (0.02, 0.08, 0.30, "tilt", "tilt_rate_high"),     # deg/day
    "tilt_level": (0.06, 0.115, 0.19, "tilt", "tilt_rate_high"),     # deg (1/500 = 0.115, 1/300 = 0.19)
    "crack_rate6": (0.3, 1.5, 5.0, "strain", "crack_opening"),       # mm/day, opening
    "crack_level": (1.0, 3.0, 10.0, "strain", "crack_opening"),      # mm
    "ext_rate6": (1.0, 4.0, 12.0, "strain", "ext_stretch"),          # mm/day over 22.5 m
    "ext_level": (3.0, 10.0, 22.0, "strain", "ext_stretch"),         # mm (22 mm / 22.5 m ~ 1 mm/m strain)
    "vib_ratio": (3.0, 6.0, 15.0, "vib", "vibration_burst"),         # x node baseline, 1 h p90
    "temp_excess": (8.0, 20.0, 40.0, "heat", "ground_heating"),      # deg C above site median
}
RATE_KEYS = ("tilt_rate6", "tilt_rate24", "crack_rate6", "ext_rate6")


def ramp(x: np.ndarray, watch: float, warning: float, critical: float) -> np.ndarray:
    xs = [0.0, watch, warning, critical, 2 * critical]
    ys = [0.0, *BOUNDS, 1.0]
    return np.interp(np.maximum(x, 0.0), xs, ys)


@dataclass
class Rules:
    thresholds: dict[str, tuple[float, float, float, str, str]] = field(
        default_factory=lambda: dict(DEFAULT_THRESHOLDS))

    def calibrate_floor(self, feats: np.ndarray, keys: tuple[str, ...], margin: float = 1.5, q: float = 99.9) -> None:
        """Raise watch thresholds of rate features above the normal-data noise floor."""
        for key in RATE_KEYS:
            if key not in keys:
                continue
            p = float(np.percentile(np.abs(feats[:, keys.index(key)]), q)) * margin
            w, wa, c, grp, code = self.thresholds[key]
            if p > w:
                scale = p / w
                self.thresholds[key] = (w * scale, wa * scale, c * scale, grp, code)

    def score(self, f: Features, faulty: np.ndarray, rates: bool = True) -> tuple[
            np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray]]:
        """Return (rules score, per-group scores, per-reason scores), each (N,)."""
        groups: dict[str, np.ndarray] = {}
        reasons: dict[str, np.ndarray] = {}
        for key, (w, wa, c, grp, code) in self.thresholds.items():
            if not rates and key in RATE_KEYS:
                continue
            s = ramp(getattr(f, key), w, wa, c)
            if grp != "heat" and grp != "vib":
                s = np.where(faulty, 0.0, s)  # a faulty sensor cannot raise deformation alarms
            groups[grp] = np.maximum(groups.get(grp, 0.0), s)
            reasons[code] = np.maximum(reasons.get(code, 0.0), s)
        deform = np.maximum(groups["tilt"], groups["strain"])
        # Vibration alone (blasting, traffic) can reach Watch but not Warning.
        vib_only = np.minimum(groups["vib"], np.where(deform >= BOUNDS[0], 1.0, 0.5))
        total = np.maximum.reduce([deform, vib_only, groups["heat"]])
        return total, groups, reasons
