# SPDX-License-Identifier: MIT
"""ESP32 node: power states, duty-cycle profiles per network architecture, and clock.

Currents (mA) are data-sheet typicals; each line names its source. The profile
functions return the average current of one node for a reporting interval,
broken down by consumer, so the report can show where the energy goes.

Clock: deep-sleep timing and timestamps run from the RTC slow clock. With the
internal 150 kHz RC oscillator (calibrated against the 40 MHz crystal at each
wake) the residual error is taken as N(0, 500 ppm) per node (ASSUMPTION; ESP-IDF
documents the RC as +-5 % uncalibrated); with an external 32.768 kHz crystal,
+-20 ppm. Timestamps are corrected whenever the node hears the gateway's time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

CURRENT_MA = {
    # ESP32 Series Datasheet v4.x, table "RF Power-Consumption Specifications" and "Power Consumption by Power Modes"
    "esp32_active_80mhz": 31.0,  # modem-sleep, CPU 80 MHz (upper bound)
    "esp32_light_sleep": 0.8,
    "esp32_deep_sleep": 0.010,  # RTC timer + RTC memory
    "esp32_wifi_tx_11n_mcs0": 240.0,  # 802.11b/g/n TX at full power, peak; ~190 mA at MCS7
    "esp32_wifi_rx": 100.0,  # 802.11b/g/n RX 95-100 mA, listening counts as RX
    "esp32_wifi_modem_sleep_dtim": 20.0,  # associated, DTIM3 power save (leaf only), ASSUMPTION avg
    # SX1276 datasheet rev 7
    "sx1276_rx": 11.5,
    "sx1276_tx_17dbm": 87.0,
    "sx1276_sleep": 0.0002,
    # MPU-6050 PS rev 3.4: accelerometer only, normal mode 500 uA; low-power cycle 5 Hz 20 uA
    "mpu6050_accel": 0.5,
    "mpu6050_cycle_5hz": 0.020,
    # VL53L0X datasheet: active ranging 19 mA average, HW standby (XSHUT low) 5 uA
    "vl53l0x_ranging": 19.0,
    "vl53l0x_standby": 0.005,
    # HT7333 quiescent current
    "ht7333_iq": 0.004,
}


def lora_airtime_s(payload: int, sf: int = 7, bw_hz: int = 125_000, cr: int = 5, preamble: int = 8) -> float:
    """Semtech AN1200.13, explicit header, CRC on (same formula as hardware/power_budget.py)."""
    t_sym = (1 << sf) / bw_hz
    de = 1 if t_sym > 0.016 else 0
    n_payload = 8 + max(math.ceil((8 * payload - 4 * sf + 28 + 16) / (4 * (sf - 2 * de))) * cr, 0)
    return (preamble + 4.25 + n_payload) * t_sym


@dataclass
class ProfileCfg:
    interval_s: float = 1.0
    vib_fs_hz: float = 200.0  # MPU6050 runs continuously at this rate when interval < 10 s
    fifo_drain_s: float = 0.4  # 1024 B FIFO holds ~0.42 s of 3-axis 200 Hz data
    cpu_per_drain_s: float = 0.003
    cpu_per_report_s: float = 0.006  # features + detection (achal_dsp) + packet
    tof_every_s: float = 10.0  # displacement is slow: range every 10 s
    tof_samples: int = 5
    tof_budget_s: float = 0.033
    payload_bytes: int = 32
    lora_sf: int = 7


def _sensing(c: ProfileCfg) -> dict[str, float]:
    """Average mA spent on sensing and on-node processing."""
    C = CURRENT_MA
    out = {}
    if c.interval_s < 30:  # continuous 200 Hz sampling, CPU drains the FIFO from light sleep
        out["mpu6050"] = C["mpu6050_accel"]
        cpu_s = c.cpu_per_drain_s / c.fifo_drain_s * c.interval_s + c.cpu_per_report_s
    else:
        # 2 s busy-wait acquisition per report, as firmware/src/node-esp32/sensors.cpp; MPU in 5 Hz cycle mode between
        window = 2.0
        out["mpu6050"] = (C["mpu6050_accel"] * window + C["mpu6050_cycle_5hz"] * (c.interval_s - window)) / c.interval_s
        cpu_s = window + c.cpu_per_report_s
    out["cpu_processing"] = C["esp32_active_80mhz"] * cpu_s / c.interval_s
    tof_s = c.tof_samples * c.tof_budget_s
    out["vl53l0x"] = (C["vl53l0x_ranging"] * tof_s + C["vl53l0x_standby"] * (c.tof_every_s - tof_s)) / c.tof_every_s
    out["cpu_during_tof"] = C["esp32_active_80mhz"] * tof_s / c.tof_every_s
    out["ldo_iq"] = C["ht7333_iq"]
    return out


def profile_ma(arch: str, role: str, c: ProfileCfg) -> dict[str, float]:
    """Average current breakdown (mA) for one node.

    arch: 'wifi_mesh' (option A / the local part of option C), 'lorawan' (option B),
    role: 'router' (forwards for children, never sleeps its radio) or 'leaf'.
    """
    C = CURRENT_MA
    out = _sensing(c)
    if arch == "wifi_mesh":
        # ESP-WIFI-MESH keeps every non-leaf node associated and listening; leaves can use DTIM power save.
        if role == "router":
            out["wifi_listen"] = C["esp32_wifi_rx"]
        else:
            out["wifi_listen"] = C["esp32_wifi_modem_sleep_dtim"]
        tx_s = (c.payload_bytes + 60) * 8 / 6.5e6 + 100e-6  # MCS0 frame + preamble + ACK turnaround
        out["wifi_tx"] = (C["esp32_wifi_tx_11n_mcs0"] - C["esp32_wifi_rx"]) * tx_s / c.interval_s
        out["cpu_idle"] = 0.0  # included in the Wi-Fi RX / modem-sleep figure
    elif arch == "lorawan":
        toa = lora_airtime_s(c.payload_bytes + 13, c.lora_sf)  # LoRaWAN MAC overhead 13 B
        rx_win = 2 * 0.012 * (1 << (c.lora_sf - 7))  # class A RX1/RX2 preamble detection windows
        out["lora_tx"] = (C["sx1276_tx_17dbm"] + C["esp32_active_80mhz"]) * toa / c.interval_s
        out["lora_rx_windows"] = (C["sx1276_rx"] + C["esp32_light_sleep"]) * rx_win / c.interval_s
        idle = C["esp32_light_sleep"] if c.interval_s < 30 else C["esp32_deep_sleep"]
        out["cpu_idle"] = idle
    else:
        raise ValueError(arch)
    return out


def total_ma(breakdown: dict[str, float]) -> float:
    return float(sum(breakdown.values()))


@dataclass
class RtcClock:
    """Node clock with a fixed frequency error; resynchronised by gateway time."""

    ppm: float
    offset_s: float = 0.0
    last_sync_s: float = 0.0

    @classmethod
    def random(cls, rng: np.random.Generator, crystal: bool = False) -> RtcClock:
        return cls(ppm=float(rng.normal(0, 20.0 if crystal else 500.0)))

    def read(self, true_s: float) -> float:
        return true_s + self.offset_s + (true_s - self.last_sync_s) * self.ppm * 1e-6

    def sync(self, true_s: float, error_s: float = 0.0) -> None:
        self.offset_s, self.last_sync_s = error_s, true_s
