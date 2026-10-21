# SPDX-License-Identifier: MIT
"""One virtual ESP32 surface node: what its sensors would report for a given true ground state.

Models sensor noise, temperature sensitivity, slow tilt wander, a ratcheting crack
gauge, an 18650 cell with a small solar panel, and injected faults (stuck, spike,
drift, battery). Magnitudes are datasheet-order assumptions for low-cost parts
(MEMS inclinometer, potentiometric crack gauge, wire extensometer, MEMS accelerometer);
see docs/field-sim-model.md.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from achal_sim.geomech import Response
from achal_sim.scenarios import Fault

CRACK_THRESHOLD_MM_PER_M = 1.0  # soil cracks once tensile strain passes this (assumption)
CRACK_GAUGE_BASE_M = 1.0        # the gauge spans 1 m of ground
CELL_AH = 3.4                   # one 18650 cell
SOLAR_PEAK_MA = 160.0           # ~1 W panel through the charger at full sun
CHARGE_EFFICIENCY = 0.85
BROWNOUT_SOC = 0.02             # below this the node stops transmitting
RESTART_SOC = 0.10
# Measuring ranges: readings saturate here (a node riding into a pothole pegs its sensors).
RANGES = {"tilt_x_deg": (-15.0, 15.0), "tilt_y_deg": (-15.0, 15.0), "crack_mm": (-5.0, 50.0),
          "ext_mm": (-200.0, 200.0), "vib_rms_g": (0.0, 2.0), "vib_peak_g": (0.0, 2.0)}

# Open-circuit voltage of a Li-ion cell vs state of charge.
OCV = ((0.0, 3.0), (0.1, 3.45), (0.2, 3.6), (0.5, 3.75), (0.8, 3.95), (1.0, 4.18))


def ocv(soc: float) -> float:
    for (s0, v0), (s1, v1) in zip(OCV, OCV[1:], strict=False):
        if soc <= s1:
            return v0 + (v1 - v0) * (max(soc, s0) - s0) / (s1 - s0)
    return OCV[-1][1]


def mm_per_m_to_deg(slope: float) -> float:
    return math.degrees(math.atan(slope / 1000.0))


@dataclass
class Environment:
    """Site-wide conditions for one sample window."""

    t_h: float
    dt_h: float
    air_temp_c: float
    solar: float  # 0..1 irradiance including cloud cover


class NodeSensors:
    def __init__(self, node: str, seed: int, fault: Fault | None = None) -> None:
        self.node = node
        self.rng = random.Random(f"{seed}:{node}:sensors")
        r = self.rng
        self.tilt_tc = (r.gauss(0, 0.0006), r.gauss(0, 0.0006))     # deg per degC
        self.crack_tc = r.choice([-1, 1]) * r.uniform(0.002, 0.006)  # mm per degC
        self.ext_tc = r.choice([-1, 1]) * r.uniform(0.005, 0.015)    # mm per degC, compensated wire
        self.temp_offset = r.gauss(0, 0.8)                           # shade, enclosure colour
        self.load_ma = 8.0 + r.uniform(-1, 1)                        # ESP32 deep sleep + LoRa TX every minute
        self.soc = r.uniform(0.80, 0.95)
        self.capacity_ah = CELL_AH
        self.wander = [0.0, 0.0]                                     # slow tilt random walk, deg
        self.crack_true = 0.0
        self.alive = True
        self.fault = fault
        self.frozen: dict[str, float] | None = None
        self.drift_rate = (r.choice([-1, 1]) * r.uniform(0.01, 0.03), r.uniform(0.1, 0.3))  # deg/day, mm/day

    def fault_active(self, t_h: float) -> str | None:
        return self.fault.kind if self.fault and t_h >= self.fault.onset_h else None

    def _battery(self, env: Environment, fault: str | None) -> None:
        if fault == "battery":  # charger failed and the cell has faded
            charge_ma, self.capacity_ah = 0.0, 0.8
        else:
            charge_ma = SOLAR_PEAK_MA * env.solar * CHARGE_EFFICIENCY
        self.soc += (charge_ma - self.load_ma) * env.dt_h / 1000.0 / self.capacity_ah
        self.soc = min(1.0, max(0.0, self.soc))
        if self.alive and self.soc <= BROWNOUT_SOC:
            self.alive = False
        elif not self.alive and self.soc >= RESTART_SOC:
            self.alive = True

    def read(self, env: Environment, ground: Response, ext_true_mm: float, heat_dt_c: float,
             vib: tuple[float, float]) -> dict | None:
        """Return the sensor fields of one telemetry message, or None if the node is browned out."""
        r = self.rng
        fault = self.fault_active(env.t_h)
        self._battery(env, fault)

        temp = env.air_temp_c + self.temp_offset + heat_dt_c + r.gauss(0, 0.15)
        d_temp = temp - 25.0
        step = math.sqrt(env.dt_h)
        self.wander = [w + r.gauss(0, 0.0002) * step for w in self.wander]

        tension = max(ground.strain_x, ground.strain_y)
        self.crack_true = max(self.crack_true, (tension - CRACK_THRESHOLD_MM_PER_M) * CRACK_GAUGE_BASE_M)

        out = {
            "tilt_x_deg": mm_per_m_to_deg(ground.tilt_x) + self.wander[0] + self.tilt_tc[0] * d_temp
            + r.gauss(0, 0.002),
            "tilt_y_deg": mm_per_m_to_deg(ground.tilt_y) + self.wander[1] + self.tilt_tc[1] * d_temp
            + r.gauss(0, 0.002),
            "crack_mm": self.crack_true + self.crack_tc * d_temp + r.gauss(0, 0.01),
            "ext_mm": ext_true_mm + self.ext_tc * d_temp + r.gauss(0, 0.03),
        }
        vib_rms, vib_peak = vib
        noise_rms = abs(r.gauss(0.0004, 0.0001))
        rms = math.sqrt(vib_rms**2 + noise_rms**2)
        out["vib_rms_g"] = rms
        out["vib_peak_g"] = max(vib_peak, rms * r.uniform(2.5, 4.0))
        out["temp_c"] = temp
        out["batt_v"] = ocv(self.soc) + r.gauss(0, 0.005)

        if fault:
            self._apply_fault(fault, out, env.t_h)
        for key, (lo, hi) in RANGES.items():
            out[key] = min(hi, max(lo, out[key]))
        if not self.alive:
            return None
        return out

    def _apply_fault(self, fault: str, out: dict, t_h: float) -> None:
        r = self.rng
        keys = ("tilt_x_deg", "tilt_y_deg", "crack_mm", "ext_mm")
        if fault == "stuck":  # sensor bus hung: the MCU keeps re-sending its last good values
            if self.frozen is None:
                self.frozen = {k: out[k] for k in keys}
            out.update(self.frozen)
        elif fault == "spike" and r.random() < 0.03:
            key = r.choice(keys)
            size = {"tilt_x_deg": 0.3, "tilt_y_deg": 0.3, "crack_mm": 10.0, "ext_mm": 15.0}[key]
            out[key] += r.choice([-1, 1]) * r.uniform(0.2, 1.0) * size
        elif fault == "drift":  # node settling on loose fill, or a creeping sensor mount
            days = (t_h - self.fault.onset_h) / 24
            out["tilt_x_deg"] += self.drift_rate[0] * days
            out["crack_mm"] += self.drift_rate[1] * days
