# SPDX-License-Identifier: MIT
"""18650 cell + TP4056 linear charger + small solar panel, simulated hour by hour.

Sources:
* Cell: Samsung INR18650-30Q data sheet (3000 mAh, 3.6 V nominal, charge 0-50 degC,
  4.20 V CV). The OCV-SOC curve is a typical NMC curve (ASSUMPTION: replace with
  a measured curve of the purchased cells).
* Protection: DW01A over-discharge 2.4 V, over-charge 4.3 V (DW01A data sheet).
* Charger: TP4056 data sheet: linear CC/CV, 4.2 V +-1 %, I_prog = 1200 V / R_prog
  (1.2 k on the common module = 1 A), termination at I_prog/10, UVLO ~3.8 V.
  A linear charger has no MPPT and no input regulation: the panel is pulled
  down to roughly V_bat + dropout, and every volt above V_bat is heat.
* Regulator: HT7333-A (250 mA max, 4 uA Iq), dropout ~ 90 mV at 40 mA scaling
  with current (ASSUMPTION: linear, 0.36 ohm equivalent).
* Panel: generic 5 V / 2 W polycrystalline (Voc 6.0 V, Vmp 5.0 V, Imp 0.40 A at STC),
  -0.4 %/degC power, NOCT 45 degC.
* Weather: NASA POWER 2001-2025 monthly climatology for Jharia
  (data/hwsim/nasa_power_jharia.json). Daily clearness is drawn around the
  monthly all-sky/clear-sky ratio with AR(1) day-to-day persistence (ASSUMPTION:
  rho = 0.6), which produces realistic multi-day monsoon gloom.
* Enclosure: sealed IP65 ABS box in the sun runs hotter than the air
  (ASSUMPTION: +25 degC at 1000 W/m^2, linear in irradiance).

This is the guide's "daily energy budget, low-battery warnings and autonomy
estimates" model. It does not prove the circuit is safe; see the hazards it
counts (charging outside 0-50 degC, regulator overload) in the report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import erf
from pathlib import Path

import numpy as np
from scipy.stats import beta as _beta

REPO = Path(__file__).resolve().parents[5]
NASA = json.loads((REPO / "data" / "hwsim" / "nasa_power_jharia.json").read_text())
LAT_DEG = 23.77
DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

SOC_PTS = np.array([0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]) / 100
OCV_PTS = np.array([3.00, 3.30, 3.45, 3.55, 3.62, 3.67, 3.72, 3.79, 3.87, 3.95, 4.05, 4.18])


def ocv(soc: float) -> float:
    return float(np.interp(soc, SOC_PTS, OCV_PTS))


@dataclass
class Cell:
    capacity_mah: float = 3000.0
    r_int_ohm: float = 0.075  # cell 25 mOhm + DW01/8205A + holder + wiring
    soc: float = 0.8
    self_discharge_per_month: float = 0.02
    protected_off: bool = False  # DW01 tripped (needs a charger to recover)

    def terminal_v(self, i_load_a: float) -> float:
        return ocv(self.soc) - i_load_a * self.r_int_ohm

    def step(self, i_net_a: float, dt_h: float) -> None:
        """i_net_a > 0 charges. Coulombic efficiency 99 % on charge."""
        mah = i_net_a * 1000 * dt_h * (0.99 if i_net_a > 0 else 1.0)
        mah -= self.capacity_mah * self.self_discharge_per_month * dt_h / (30 * 24)
        self.soc = float(np.clip(self.soc + mah / self.capacity_mah, 0.0, 1.0))
        if self.terminal_v(max(-i_net_a, 0)) <= 2.4:
            self.protected_off = True
        if self.protected_off and i_net_a > 0 and ocv(self.soc) > 3.0:
            self.protected_off = False


@dataclass
class Panel:
    p_stc_w: float = 2.0
    voc: float = 6.0
    vmp: float = 5.0
    imp: float = 0.40
    soiling: float = 0.15  # coal dust on the glass (ASSUMPTION; sweep it)
    a_v: float = 0.417  # diode-curve knee fitted to Vmp/Imp/Voc

    @property
    def isc(self) -> float:
        scale = self.p_stc_w / 2.0
        return self.imp * scale / (1 - np.exp((self.vmp - self.voc) / self.a_v))

    def current(self, v: float, g_wm2: float, t_cell_c: float) -> float:
        if g_wm2 <= 1:
            return 0.0
        g = g_wm2 * (1 - self.soiling)
        voc = self.voc + self.a_v * np.log(g / 1000) - 0.0035 * self.voc * (t_cell_c - 25)
        return max(self.isc * g / 1000 * (1 - np.exp((v - voc) / self.a_v)), 0.0)


@dataclass
class Tp4056:
    i_prog_a: float = 1.0
    v_cv: float = 4.2
    dropout_v: float = 0.15
    uvlo_v: float = 3.8
    ntc_wired: bool = False  # the common module grounds TEMP: no temperature cut-off

    def charge_current(
        self, cell: Cell, panel: Panel, g: float, t_cell_c: float, t_batt_c: float
    ) -> tuple[float, float]:
        """Returns (current into the cell A, power taken from the panel W)."""
        if g <= 1:
            return 0.0, 0.0
        if self.ntc_wired and not (0.0 <= t_batt_c <= 45.0):
            return 0.0, 0.0
        v_oc = ocv(cell.soc)
        if cell.soc >= 0.995:
            return 0.0, 0.0  # charge terminated (CV current fell below I_prog / 10)
        # CC until the terminal voltage reaches V_cv, then CV: the current tapers with the headroom.
        cv_headroom = max(self.v_cv - v_oc, 0.0) + 0.02 * (1 - cell.soc)
        target = min(self.i_prog_a, cv_headroom / cell.r_int_ohm)
        v_in = max(v_oc + target * cell.r_int_ohm + self.dropout_v, self.uvlo_v)
        i_avail = panel.current(v_in, g, t_cell_c)
        if i_avail <= 0:
            return 0.0, 0.0
        i = min(target, i_avail)
        return i, i * v_in


# --- weather -----------------------------------------------------------------


def _declination(doy: int) -> float:
    return np.radians(23.45) * np.sin(2 * np.pi * (284 + doy) / 365)


def hourly_cos_zenith(doy: int, lat_deg: float = LAT_DEG) -> np.ndarray:
    lat, dec = np.radians(lat_deg), _declination(doy)
    hour_angle = np.radians(15 * (np.arange(24) + 0.5 - 12))
    return np.clip(np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(hour_angle), 0, None)


def poa_factor(doy: int, kt: float, tilt_deg: float = LAT_DEG, lat_deg: float = LAT_DEG) -> np.ndarray:
    """Hourly plane-of-array / horizontal ratio, south-facing panel, Erbs split, isotropic sky, albedo 0.2."""
    lat, dec, beta = np.radians(lat_deg), _declination(doy), np.radians(tilt_deg)
    w = np.radians(15 * (np.arange(24) + 0.5 - 12))
    cz = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(w)
    ci = np.sin(lat - beta) * np.sin(dec) + np.cos(lat - beta) * np.cos(dec) * np.cos(w)
    kd = (
        1.0 - 0.09 * kt
        if kt <= 0.22
        else (0.9511 - 0.1604 * kt + 4.388 * kt**2 - 16.638 * kt**3 + 12.336 * kt**4 if kt <= 0.8 else 0.165)
    )
    rb = np.where(cz > 0.05, np.clip(ci, 0, None) / np.where(cz > 0.05, cz, 1), 0)
    return (1 - kd) * rb + kd * (1 + np.cos(beta)) / 2 + 0.2 * (1 - np.cos(beta)) / 2


@dataclass
class Weather:
    """One synthetic year of hourly irradiance (W/m^2 on the panel) and air temperature."""

    seed: int
    rho: float = 0.6
    g_poa: np.ndarray = field(init=False)
    t_air: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        g, t = [], []
        z = 0.0
        doy = 0
        for m, nd in enumerate(DAYS_IN_MONTH):
            clr, allsky = NASA["CLRSKY_SFC_SW_DWN"][m], NASA["ALLSKY_SFC_SW_DWN"][m]
            mean_k = allsky / clr
            # Beta-distributed daily clear-sky fraction with the monthly mean; spread 0.2 (ASSUMPTION).
            var = min(0.2**2, mean_k * (1 - mean_k) * 0.9)
            ab = mean_k * (1 - mean_k) / var - 1
            a, b = mean_k * ab, (1 - mean_k) * ab
            t_mean = NASA["T2M"][m]
            amp = 7.0 if m in (2, 3, 4) else 4.0 if m in (5, 6, 7, 8) else 5.5  # diurnal half-range (ASSUMPTION)
            for _ in range(nd):
                doy += 1
                z = self.rho * z + np.sqrt(1 - self.rho**2) * rng.normal()
                u = 0.5 * (1 + erf(z / np.sqrt(2)))
                k = float(_beta.ppf(np.clip(u, 1e-4, 1 - 1e-4), a, b))
                cz = hourly_cos_zenith(doy)
                ghi_day_wh = clr * 1000 * k
                shape = cz / cz.sum() if cz.sum() > 0 else cz
                ghi = ghi_day_wh * shape  # Wh in each hour == mean W over the hour
                kt = min(0.8, 0.75 * k)
                g.append(ghi * poa_factor(doy, kt))
                hours = np.arange(24)
                t.append(t_mean + rng.normal(0, 1.5) + amp * np.sin(2 * np.pi * (hours - 9) / 24))
        self.g_poa = np.concatenate(g)
        self.t_air = np.concatenate(t)


@dataclass
class YearResult:
    soc: np.ndarray
    online: np.ndarray
    low_batt_warn: np.ndarray
    unsafe_charge_h: int
    harvested_wh: float
    consumed_wh: float

    @property
    def downtime_h(self) -> int:
        return int((~self.online).sum())

    @property
    def min_soc(self) -> float:
        return float(self.soc.min())


def simulate_year(
    load_ma: float,
    weather: Weather,
    panel: Panel | None = None,
    cell: Cell | None = None,
    charger: Tp4056 | None = None,
    shutdown_v: float = 3.30,
    restart_v: float = 3.60,
    warn_v: float = 3.45,
    sleep_ma_when_off: float = 0.02,
    box_rise_c: float = 25.0,
) -> YearResult:
    """Hourly energy balance for one node drawing load_ma on average while online."""
    panel, cell, charger = panel or Panel(), cell or Cell(), charger or Tp4056()
    n = len(weather.g_poa)
    soc, online, warn = np.zeros(n), np.zeros(n, bool), np.zeros(n, bool)
    up, unsafe, harv, cons = True, 0, 0.0, 0.0
    for h in range(n):
        g, ta = weather.g_poa[h], weather.t_air[h]
        t_box = ta + box_rise_c * g / 1000
        t_cell = ta + g / 800 * 25  # NOCT 45 degC
        i_chg, p_in = charger.charge_current(cell, panel, g, t_cell, t_box)
        if i_chg > 0 and not (0.0 <= t_box <= 50.0):
            unsafe += 1
        v_load = cell.terminal_v(load_ma / 1000)
        if up and v_load < shutdown_v:
            up = False
        elif not up and ocv(cell.soc) > restart_v and not cell.protected_off:
            up = True
        i_load = (load_ma if up else sleep_ma_when_off) / 1000
        if cell.protected_off:
            i_load = 0.0
        soc_before = cell.soc
        cell.step(i_chg - i_load, 1.0)
        if cell.soc >= 1.0 and i_chg > i_load:
            i_chg = i_load + (cell.soc - soc_before) * cell.capacity_mah / 1000  # only what was stored
        harv += i_chg * ocv(cell.soc)
        cons += i_load * ocv(cell.soc)
        soc[h], online[h] = cell.soc, up
        warn[h] = up and cell.terminal_v(load_ma / 1000) < warn_v
    return YearResult(soc, online, warn, unsafe, harv, cons)
