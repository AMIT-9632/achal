# SPDX-License-Identifier: MIT
"""InvenSense MPU-6050 accelerometer model (gyroscope off, as in the node firmware).

Parameters are from the MPU-6000/MPU-6050 Product Specification rev 3.4,
section 6.2 (accelerometer), unless marked ASSUMPTION:

* +-2 g range, 16384 LSB/g, 16-bit two's-complement output, saturating.
* Initial sensitivity tolerance +-3 %, cross-axis sensitivity +-2 %, nonlinearity 0.5 %.
* Zero-g output: +-50 mg (X, Y), +-80 mg (Z) at 25 degC.
* Zero-g change over 0..70 degC: +-35 mg (X, Y), +-60 mg (Z).
* Noise power spectral density 400 ug/sqrt(Hz) at 10 Hz.
* Digital low-pass filter bandwidths 260/184/94/44/21/10/5 Hz (register CONFIG).
* Temperature sensor 340 LSB/degC, 35 degC at -521 LSB.

What is *not* in the datasheet and is therefore an assumption, kept in one
place so a bench test can replace it:

* Per-unit temperature coefficient of the zero-g offset, drawn N(0, 0.35 mg/degC)
  per axis, so that two sigma over a 35 degC span stays inside the +-35 mg spec.
* Slow bias wander: first-order Gauss-Markov, sigma 0.2 mg, tau 1 h.

The installed node sits on a ground stake. True ground tilt rotates gravity in
the sensor frame; ground vibration adds to the vertical axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

LSB_PER_G = 16384.0
NOISE_PSD_G = 400e-6
DLPF_BW_HZ = {0: 260.0, 1: 184.0, 2: 94.0, 3: 44.0, 4: 21.0, 5: 10.0, 6: 5.0}
ZERO_G_MG = np.array([50.0, 50.0, 80.0])
ZERO_G_TC_SPEC_MG = np.array([35.0, 35.0, 60.0])  # over 0..70 degC
TC_SIGMA_MG_PER_C = np.array([0.35, 0.35, 0.6])  # ASSUMPTION, see module doc
WANDER_SIGMA_MG, WANDER_TAU_S = 0.2, 3600.0  # ASSUMPTION


@dataclass
class Mpu6050:
    rng: np.random.Generator
    dlpf_cfg: int = 2  # 94 Hz, as firmware/src/node-esp32/sensors.cpp
    t_ref_c: float = 25.0
    offset_g: np.ndarray = field(init=False)
    tc_g_per_c: np.ndarray = field(init=False)
    gain: np.ndarray = field(init=False)
    cross: np.ndarray = field(init=False)
    _wander: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        r = self.rng
        # Datasheet limits treated as 3 sigma for the zero-g offset and gain.
        self.offset_g = r.normal(0, ZERO_G_MG / 3) * 1e-3
        self.tc_g_per_c = r.normal(0, TC_SIGMA_MG_PER_C) * 1e-3
        self.gain = 1 + r.normal(0, 0.03 / 3, 3)
        self.cross = r.normal(0, 0.02 / 3, (3, 3))
        np.fill_diagonal(self.cross, 0.0)
        self._wander = np.zeros(3)

    @property
    def noise_sigma_g(self) -> float:
        """White-noise sigma per sample: PSD x sqrt(equivalent noise bandwidth)."""
        return NOISE_PSD_G * np.sqrt(1.57 * DLPF_BW_HZ[self.dlpf_cfg])

    def _advance_wander(self, dt_s: float) -> None:
        a = np.exp(-dt_s / WANDER_TAU_S)
        self._wander = a * self._wander + np.sqrt(1 - a * a) * self.rng.normal(0, WANDER_SIGMA_MG * 1e-3, 3)

    def sample_window(
        self,
        tilt_x_deg: float,
        tilt_y_deg: float,
        temp_c: float,
        n: int,
        fs_hz: float,
        vib_z_g: np.ndarray | None = None,
        gap_s: float = 1.0,
    ) -> tuple[np.ndarray, int]:
        """One acquisition window. Returns (int16 counts, shape (n, 3)), TEMP_OUT counts).

        gap_s is the time since the previous window, for the bias wander.
        """
        self._advance_wander(gap_s)
        tx, ty = np.radians(tilt_x_deg), np.radians(tilt_y_deg)
        # Gravity in the sensor frame; inverse of achal_tilt_deg for small and moderate tilts.
        gx, gy = np.sin(tx), np.sin(ty)
        gz = np.sqrt(max(1.0 - gx * gx - gy * gy, 0.0))
        true = np.tile([gx, gy, gz], (n, 1))
        if vib_z_g is not None:
            true[:, 2] += vib_z_g[:n]
        sensed = true @ (np.diag(self.gain) + self.cross).T
        bias = self.offset_g + self.tc_g_per_c * (temp_c - self.t_ref_c) + self._wander
        sensed += bias + self.rng.normal(0, self.noise_sigma_g, (n, 3))
        sensed += 0.005 / 3 * sensed**2 * np.sign(sensed)  # 0.5 % nonlinearity, 3 sigma at full scale
        counts = np.clip(np.round(sensed * LSB_PER_G), -32768, 32767).astype(np.int16)
        temp_counts = int(round((temp_c - 36.53) * 340.0 + self.rng.normal(0, 3)))
        return counts, temp_counts

    def calibrate_tc(self, err_frac: float = 0.15) -> np.ndarray:
        """Two-temperature factory calibration: returns the measured coefficient with a relative error."""
        return self.tc_g_per_c * (1 + self.rng.normal(0, err_frac, 3))


def tilt_noise_floor_deg(n_samples: int, dlpf_cfg: int = 2) -> float:
    """1-sigma tilt resolution from white noise alone for an n-sample mean."""
    sigma = NOISE_PSD_G * np.sqrt(1.57 * DLPF_BW_HZ[dlpf_cfg]) / np.sqrt(n_samples)
    return float(np.degrees(sigma))
