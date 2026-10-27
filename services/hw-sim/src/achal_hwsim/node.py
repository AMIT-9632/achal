# SPDX-License-Identifier: MIT
"""Stage 2 in Python: one virtual sensor node, from true ground motion to a packet.

Per reporting window the node:
 1. acquires a window of MPU6050 counts at vib_fs_hz (components.mpu6050);
 2. converts counts to g, applies the per-unit temperature compensation using the
    MPU's own die temperature, averages to a gravity vector and computes tilt;
 3. computes vibration features (RMS, peak, PPV, dominant frequency);
 4. every tof_every_s, fires a VL53L0X burst and median-filters it;
 5. runs the detector; steps 2-5 call firmware/lib/achal_dsp through ctypes;
 6. timestamps the packet with its own drifting RTC (components.esp32.RtcClock);
 7. charges or drains its 18650 (components.power) and stops when it browns out.

Simulation-only ground truth travels in packet["truth"] and is never read by the
gateway's decision logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import dsp
from .components.esp32 import ProfileCfg, RtcClock, profile_ma, total_ma
from .components.mpu6050 import Mpu6050
from .components.power import Cell
from .components.vl53l0x import TofConditions, Vl53l0x
from .scenarios import Scenario

LOW_BATT_MV, SHUTDOWN_MV = 3450, 3300
F_TILT_SENSOR, F_TOF, F_LOW_BATT, F_NO_SYNC, F_MOTION, F_VIB_EXCEED = 1, 2, 4, 16, 32, 64


@dataclass
class NodeCfg:
    interval_s: float = 1.0
    vib_fs_hz: float = 200.0
    vib_window_s: float = 1.0
    tilt_tau_s: float = 10.0  # EMA on tilt between windows (achal_ema)
    min_window_frac: float = 0.5  # a window with fewer samples is discarded, not used
    tof_every_s: float = 30.0
    tof_samples: int = 7
    tof_mode: str = "high_accuracy"
    tof_gauge_mm: float = 500.0  # sensor to target plate on the reference stake
    tof_plate_half_mm: float = 125.0  # 250 mm square white plate fills the 25 deg cone at 0.5 m
    tof_ambient: str = "shaded_tube"
    tof_baseline_bursts: int = 5  # install: 5 bursts are median-filtered into the ToF baseline
    tof_recal_delta_c: float = 4.0  # re-run the ToF reference calibration after this temperature change
    temp_comp: bool = True
    baseline_windows: int = 10
    arch: str = "wifi_mesh"
    role: str = "leaf"
    crystal_rtc: bool = False
    det_overrides: dict = field(default_factory=dict)


class VirtualNode:
    def __init__(self, idx: int, sc: Scenario, cfg: NodeCfg, seed: int, soc0: float = 0.8):
        self.idx, self.sc, self.cfg = idx, sc, cfg
        self.name = f"n{idx + 1:02d}"
        rng = np.random.default_rng([seed, idx])
        self.rng = rng
        self.mpu = Mpu6050(rng)
        self.tc_cal = self.mpu.calibrate_tc() if cfg.temp_comp else np.zeros(3)
        self.tof = Vl53l0x(rng, cfg.tof_mode)
        self.tof_cfg = dsp.tof_cfg(
            min_valid=3, max_sigma_mm=15.0, min_signal_mcps=0.25, max_range_mm=int(cfg.tof_gauge_mm + 300)
        )
        self.det = dsp.Detector(dsp.det_cfg(**cfg.det_overrides))
        self.clock = RtcClock.random(rng, cfg.crystal_rtc)
        self.cell = Cell(soc=soc0)
        self.load_ma = total_ma(
            profile_ma(cfg.arch, cfg.role, ProfileCfg(interval_s=cfg.interval_s, tof_every_s=cfg.tof_every_s))
        )
        self.seq = 0
        self.alive = True
        self._base_tilt: list[tuple[float, float]] = []
        self._base_tof: list[float] = []
        self.base_tilt: tuple[float, float] | None = None
        self.base_tof: float | None = None
        self.tof_mm: float | None = None
        self.tof_valid_n = 0
        self.last_tilt = (0.0, 0.0)
        self._drift_g = 0.0
        self._ema: tuple[float | None, float | None] = (None, None)
        self._tof_hist: list[float] = []
        self.mpu.t_ref_c = sc.temp0_c
        self.tof.ref_calibrate(sc.temp0_c)

    def _tilt(
        self, t: float, temp_c: float, tilt_true: tuple[float, float], vib: np.ndarray, missing: bool
    ) -> tuple[tuple[float, float] | None, dsp.Vib | None, int]:
        n = int(self.cfg.vib_fs_hz * self.cfg.vib_window_s)
        counts, temp_counts = self.mpu.sample_window(
            tilt_true[0], tilt_true[1], temp_c, n, self.cfg.vib_fs_hz, vib, gap_s=self.cfg.interval_s
        )
        if missing:  # I2C NACK part way through: keep what arrived, flag it
            cut = int(self.rng.integers(0, n))
            if cut < self.cfg.min_window_frac * n:
                return None, None, F_TILT_SENSOR
            counts = counts[:cut]
        g = counts.astype(np.float64) / 16384.0
        g[:, 0] += self._drift_g
        die_c = dsp.mpu_temp_c(temp_counts)
        mean = g.mean(0)
        comp = [dsp.temp_comp(float(mean[k]), float(self.tc_cal[k]), die_c, self.mpu.t_ref_c) for k in range(3)]
        tx, ty = dsp.tilt_deg(*comp)
        a = self.cfg.interval_s / (self.cfg.tilt_tau_s + self.cfg.interval_s)
        self._ema = (dsp.ema(self._ema[0], tx, a), dsp.ema(self._ema[1], ty, a))
        tx, ty = self._ema
        vibf = dsp.vib_features(g[:, 2], self.cfg.vib_fs_hz)
        return (tx, ty), vibf, 0

    def _range(self, t: float, crack_mm: float, temp_c: float, tilt_true: tuple[float, float]) -> int:
        c = TofConditions(
            plate_half_mm=self.cfg.tof_plate_half_mm,
            ambient=self.cfg.tof_ambient,
            background_mm=self.cfg.tof_gauge_mm + 400,
            temp_c=temp_c,
        )
        faults = {f.kind: f for f in self.sc.faults_at(self.idx, t)}
        if "tof_rain" in faults:
            c.spurious_prob = faults["tof_rain"].rate
        if abs(temp_c - self.tof.t_cal_c) > self.cfg.tof_recal_delta_c:
            self.tof.ref_calibrate(temp_c)
        d = self.cfg.tof_gauge_mm + crack_mm
        # Node-stake tilt swings the beam across the plate: offset = gauge length x tan(tilt).
        offset = d * np.tan(np.radians(tilt_true[0]))
        bursts = self.cfg.tof_baseline_bursts if self.base_tof is None and not self._base_tof else 1
        shots = [s for _ in range(bursts) for s in self.tof.burst(d, c, self.cfg.tof_samples, offset)]
        if "tof_invalid" in faults:
            shots = [(8190, 4, 0.0, 0.0) if self.rng.random() < faults["tof_invalid"].rate else s for s in shots]
        mm, nv = dsp.tof_filter(self.tof_cfg, shots)
        self.tof_valid_n = nv
        self.tof_mm = mm
        if mm is not None and self.base_tof is not None:
            self._tof_hist = (self._tof_hist + [mm])[-3:]
        return 0 if mm is not None else F_TOF

    def step(self, t: float) -> dict | None:
        """One reporting window ending at true time t. Returns the packet, or None if the node is down."""
        sc, cfg = self.sc, self.cfg
        faults = {f.kind: f for f in sc.faults_at(self.idx, t)}
        dt_h = cfg.interval_s / 3600
        leak = faults["batt_leak"].rate / 1000 if "batt_leak" in faults else 0.0  # water ingress, failing cell
        self.cell.step(-(self.load_ma / 1000 if self.alive else 0.00002) - leak, dt_h)
        v_batt = self.cell.terminal_v(self.load_ma / 1000)
        if "dead" in faults or self.cell.protected_off:
            self.alive = False
            return None
        if v_batt * 1000 < SHUTDOWN_MV:
            self.alive = False
            return None
        self.alive = True
        temp_c = sc.temp_c(t)
        tx_t, ty_t, crack = sc.ground(self.idx, t)
        if "accel_drift" in faults:
            self._drift_g = faults["accel_drift"].rate * (t - faults["accel_drift"].t0)
        tw = t - cfg.vib_window_s + np.arange(int(cfg.vib_fs_hz * cfg.vib_window_s)) / cfg.vib_fs_hz
        vib = sc.vib_g(self.idx, tw)
        missing = "missing" in faults and self.rng.random() < faults["missing"].rate
        tilt, vibf, flags = self._tilt(t, temp_c, (tx_t, ty_t), vib, missing)
        if self.seq % max(int(round(cfg.tof_every_s / cfg.interval_s)), 1) == 0:
            flags |= self._range(t, crack, temp_c, (tx_t, ty_t))
        elif self.tof_mm is None:
            flags |= F_TOF

        # Install baseline from the first windows (the firmware's store_baseline()).
        if self.base_tilt is None:
            if tilt is not None:
                self._base_tilt.append(tilt)
            if self.tof_mm is not None:
                self._base_tof.append(self.tof_mm)
            if len(self._base_tilt) >= cfg.baseline_windows:
                self.base_tilt = tuple(np.median(np.array(self._base_tilt), 0))
                self.base_tof = float(np.median(self._base_tof)) if self._base_tof else None
        tilt_rel = (0.0, 0.0)
        if tilt is not None and self.base_tilt is not None:
            tilt_rel = (tilt[0] - self.base_tilt[0], tilt[1] - self.base_tilt[1])
            self.last_tilt = tilt_rel
        elif tilt is None:
            tilt_rel = self.last_tilt
        disp = None
        if len(self._tof_hist) == 3 and self.base_tof is not None:
            disp = dsp.median3(*self._tof_hist) - self.base_tof
        if self.base_tilt is None:
            # Baseline still settling: the detector is not stepped
            level, cause = 0, 0
        else:
            level = self.det.step(
                float(np.hypot(*tilt_rel)), disp, vibf.ppv_mm_s if vibf else 0.0, vibf.dom_hz if vibf else 0.0
            )
            cause = self.det.cause
        if vibf is not None and vibf.ppv_mm_s >= dsp.dgms_ppv_limit(vibf.dom_hz):
            flags |= F_VIB_EXCEED  # per window, no persistence: a blast or collapse burst is a transient
        batt_mv = int(v_batt * 1000)
        if batt_mv < LOW_BATT_MV:
            flags |= F_LOW_BATT
        pkt = {
            "node": self.name,
            "idx": self.idx,
            "seq": self.seq,
            "t_true": t,
            "ts_node": self.clock.read(t),
            "tilt_x_deg": tilt_rel[0],
            "tilt_y_deg": tilt_rel[1],
            "vib_rms_g": vibf.rms_g if vibf else None,
            "vib_peak_g": vibf.peak_g if vibf else None,
            "ppv_mm_s": vibf.ppv_mm_s if vibf else None,
            "dom_hz": vibf.dom_hz if vibf else None,
            "tof_mm": self.tof_mm,
            "disp_mm": disp,
            "tof_valid": self.tof_valid_n,
            "temp_c": temp_c,
            "batt_mv": batt_mv,
            "soc": self.cell.soc,
            "level": level,
            "cause": cause,
            "flags": flags,
            "truth": {
                "tilt_deg": float(np.hypot(tx_t, ty_t)),
                "crack_mm": crack,
                "label": sc.label(self.idx, t),
                "vib_exceed": sc.vib_exceed(self.idx, t - cfg.interval_s, t),
            },
        }
        self.seq += 1
        return pkt

    def resync(self, t: float) -> None:
        self.clock.sync(t, float(self.rng.normal(0, 0.002)))
