# SPDX-License-Identifier: MIT
"""ST VL53L0X time-of-flight ranging model.

Datasheet anchors (VL53L0X datasheet DocID029104, UM2039 API manual):

* 940 nm VCSEL, 25 degree field of view; ranging up to ~2 m in long-range
  mode on a white target indoors, ~1.2 m in default mode.
* API limit checks: signal rate >= 0.25 MCPS (default) or 0.1 MCPS (long range);
  sigma <= 18 mm (default) or 60 mm (long range); timing budget 33 ms default,
  200 ms high accuracy, 20 ms high speed.
* Out-of-range readings return 8190/8191 mm with a range status other than 0.
* Ambient infrared (sunlight) is the dominant noise source outdoors and
  shortens the maximum range sharply.

The model is a photon-budget model, calibrated to those anchors:

    signal  S = S_ref * (rho / 0.88) * cos(theta) * coverage * (d_ref / d)^2
    sigma   = sigma_ref * sqrt(S_ref / S) * sqrt(1 + A / S) * sqrt(33 / budget_ms)

with S_ref = 0.25 MCPS at d_ref = 1200 mm for a white (88 %) target (so the
default-mode limit sits at 1.2 m and the long-range limit near 1.9 m),
sigma_ref = 12 mm at that point (ASSUMPTION, ~1 %), and A the ambient rate.
`coverage` is the fraction of the FoV footprint the target plate fills; the
rest of the return comes from the background and biases the range towards it
(mixed-pixel effect). Rain/dust can produce short spurious returns.

Temperature shifts the range by RANGE_TC_MM_PER_C per degC away from the last
reference calibration (ASSUMPTION; ST's UM2039 asks for VL53L0X_PerformRefCalibration
after an 8 degC change, so the firmware re-runs it after 4 degC).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FOV_DEG = 25.0
S_REF_MCPS, D_REF_MM, SIGMA_REF_MM = 0.25, 1200.0, 12.0
RANGE_TC_MM_PER_C = 0.3  # ASSUMPTION
OUT_OF_RANGE_MM = 8190
MIN_RANGE_MM = 30

MODES = {
    # name: (timing budget ms, signal limit MCPS, sigma limit mm, average current mA while ranging)
    "default": (33.0, 0.25, 18.0, 19.0),
    "high_accuracy": (200.0, 0.25, 18.0, 19.0),
    "long_range": (33.0, 0.10, 60.0, 19.0),
    "high_speed": (20.0, 0.25, 32.0, 19.0),
}

# Ambient return rate (MCPS-equivalent) by installation. ASSUMPTION: chosen so that the
# open-sky cases reproduce ST's statement that outdoor range drops to a fraction of indoor.
AMBIENT_MCPS = {"dark": 0.0, "shaded_tube": 0.3, "overcast": 2.0, "direct_sun": 12.0}


@dataclass
class TofConditions:
    reflectance: float = 0.88  # white target plate
    incidence_deg: float = 0.0
    ambient: str = "shaded_tube"
    plate_half_mm: float = 60.0  # half-width of a square target plate
    background_mm: float | None = None  # what the rest of the cone sees (None = nothing within range)
    temp_c: float = 25.0
    spurious_prob: float = 0.0  # chance of a short false return (rain, dust, insects)


class Vl53l0x:
    def __init__(self, rng: np.random.Generator, mode: str = "high_accuracy", offset_mm: float | None = None):
        self.rng = rng
        self.mode = mode
        self.budget_ms, self.signal_limit, self.sigma_limit, self.current_ma = MODES[mode]
        # Part-to-part offset after ST's offset calibration: ASSUMPTION +-3 mm (1 sigma 1 mm).
        self.offset_mm = rng.normal(0, 1.0) if offset_mm is None else offset_mm
        self.t_cal_c = 25.0

    def ref_calibrate(self, temp_c: float) -> None:
        """VL53L0X_PerformRefCalibration: removes the temperature-induced range shift at this temperature."""
        self.t_cal_c = temp_c

    @staticmethod
    def coverage(d_mm: float, plate_half_mm: float, tilt_offset_mm: float = 0.0) -> float:
        """Fraction of the FoV footprint (a disc) covered by a square plate, beam centre shifted by tilt_offset."""
        r = d_mm * np.tan(np.radians(FOV_DEG / 2))
        if r <= 0:
            return 1.0
        # Overlap of an axis-aligned square [-h+o, h+o] x [-h, h] with a disc of radius r, on a grid.
        g = np.linspace(-r, r, 41)
        xx, yy = np.meshgrid(g, g)
        disc = xx**2 + yy**2 <= r * r
        inside = (np.abs(xx - tilt_offset_mm) <= plate_half_mm) & (np.abs(yy) <= plate_half_mm)
        return float((disc & inside).sum() / disc.sum())

    def signal_mcps(self, d_mm: float, c: TofConditions, cov: float) -> float:
        d = max(d_mm, 1.0)
        return S_REF_MCPS * (c.reflectance / 0.88) * np.cos(np.radians(c.incidence_deg)) * cov * (D_REF_MM / d) ** 2

    def measure(self, d_mm: float, c: TofConditions, tilt_offset_mm: float = 0.0) -> tuple[int, int, float, float]:
        """One ranging. Returns (range_mm, status, signal_mcps, sigma_mm) like the ST API."""
        r = self.rng
        if c.spurious_prob and r.random() < c.spurious_prob:
            # Backscatter from a raindrop or dust close to the cover glass: a short, plausible-looking range.
            d_false = r.uniform(40, 0.6 * d_mm)
            return int(d_false), 0, float(r.uniform(0.3, 2.0)), float(r.uniform(3, 10))
        cov = self.coverage(d_mm, c.plate_half_mm, tilt_offset_mm)
        s_plate = self.signal_mcps(d_mm, c, cov)
        s_bg, d_eff = 0.0, d_mm
        if c.background_mm is not None and cov < 1.0:
            bg = TofConditions(reflectance=0.25, ambient=c.ambient)  # soil / vegetation
            s_bg = self.signal_mcps(c.background_mm, bg, 1.0 - cov)
        s = s_plate + s_bg
        amb = AMBIENT_MCPS[c.ambient]
        if s <= 1e-6:
            return OUT_OF_RANGE_MM, 2, 0.0, 999.0
        if s_bg > 0:
            d_eff = (s_plate * d_mm + s_bg * c.background_mm) / s  # mixed pixel: signal-weighted
        sigma = SIGMA_REF_MM * np.sqrt(S_REF_MCPS / s) * np.sqrt(1 + amb / s) * np.sqrt(33.0 / self.budget_ms)
        s_meas = max(s * (1 + r.normal(0, 0.05)), 0.0)
        if d_mm < MIN_RANGE_MM:
            return int(max(d_mm, 0)), 3, s_meas, sigma
        if s_meas < self.signal_limit:
            return OUT_OF_RANGE_MM, 2, s_meas, sigma
        if sigma > self.sigma_limit:
            return int(np.clip(d_eff + r.normal(0, sigma), 0, 8189)), 1, s_meas, sigma
        if d_eff > 2600:  # beyond the phase-unwrap range
            return OUT_OF_RANGE_MM, 4, s_meas, sigma
        rng_mm = d_eff + self.offset_mm + RANGE_TC_MM_PER_C * (c.temp_c - self.t_cal_c) + r.normal(0, sigma)
        return int(np.clip(round(rng_mm), 0, 8189)), 0, s_meas, sigma

    def burst(self, d_mm: float, c: TofConditions, n: int, tilt_offset_mm: float = 0.0) -> list[tuple]:
        return [self.measure(d_mm, c, tilt_offset_mm) for _ in range(n)]

    def max_range_mm(self, c: TofConditions) -> float:
        """Largest distance where both the signal and the sigma limit checks pass (full plate coverage)."""
        lo, hi = 10.0, 5000.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            s = self.signal_mcps(mid, c, 1.0)
            sigma = (
                SIGMA_REF_MM
                * np.sqrt(S_REF_MCPS / s)
                * np.sqrt(1 + AMBIENT_MCPS[c.ambient] / s)
                * np.sqrt(33.0 / self.budget_ms)
            )
            ok = s >= self.signal_limit and sigma <= self.sigma_limit
            lo, hi = (mid, hi) if ok else (lo, mid)
        return lo
