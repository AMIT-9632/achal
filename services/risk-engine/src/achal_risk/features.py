# SPDX-License-Identifier: MIT
"""Per-node features computed from the recent window, vectorised over nodes.

Rates come from robust, temperature-compensated least squares:
  1. over 24 h fit  y = a + b*t + g*(T - mean T)  to learn the thermal coefficient g
     (24 h holds a full diurnal cycle, so g is not confounded with the trend);
  2. subtract g*T, then fit y = a + b*t over 6 h and 24 h;
  3. drop points more than 6 robust sigmas from the line and refit (spike-proof).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

from achal_risk.store import BT, CR, EX, TP, TX, TY, VB

DEFORM = (TX, TY, CR, EX)
MAD_K = 1.4826


def nanmedian(a: np.ndarray, axis: int = 0) -> np.ndarray:
    """Fast NaN-ignoring median (np.nanmedian is very slow on wide arrays). NaN if all missing."""
    a = np.moveaxis(np.asarray(a, float), axis, 0)
    s = np.sort(a, axis=0)  # NaN sorts last
    cnt = (~np.isnan(a)).sum(0)
    lo = np.maximum((cnt - 1) // 2, 0)[None]
    hi = np.maximum(cnt // 2, 0)[None]
    hi = np.minimum(hi, max(len(s) - 1, 0))
    if len(s) == 0:
        return np.full(a.shape[1:], np.nan)
    m = 0.5 * (np.take_along_axis(s, lo, 0)[0] + np.take_along_axis(s, hi, 0)[0])
    return np.where(cnt > 0, m, np.nan)


def _wls_line(t: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Weighted least squares y = a + b*t along axis 0. t: (k,), y/w: (k, ...). Returns (a, b)."""
    tt = t.reshape((-1,) + (1,) * (y.ndim - 1))
    sw = w.sum(0)
    st = (w * tt).sum(0)
    sy = (w * y).sum(0)
    stt = (w * tt * tt).sum(0)
    sty = (w * tt * y).sum(0)
    den = sw * stt - st * st
    with np.errstate(invalid="ignore", divide="ignore"):
        b = np.where(den > 1e-12, (sw * sty - st * sy) / den, 0.0)
        a = np.where(sw > 0, (sy - b * st) / np.maximum(sw, 1e-12), 0.0)
    return a, b


def robust_slope(t: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Robust slope of y (k, ...) against t (k,), ignoring NaN.

    Returns (slope, robust residual sigma, number of rejected outliers)."""
    w = (~np.isnan(y)).astype(float)
    y0 = np.nan_to_num(y)
    a, b = _wls_line(t, y0, w)
    tt = t.reshape((-1,) + (1,) * (y.ndim - 1))
    r = np.where(w > 0, y0 - (a + b * tt), np.nan)
    sig = np.nan_to_num(MAD_K * nanmedian(np.abs(r)))
    out = (np.abs(np.nan_to_num(r)) > 6 * sig + 1e-12) & (w > 0)
    w2 = w * ~out
    _, b2 = _wls_line(t, y0, w2)
    return b2, sig, out.sum(0)


def thermal_coefficients(t: np.ndarray, y: np.ndarray, temp: np.ndarray) -> np.ndarray:
    """g in y = a + b*t + c*t^2 + g*(T - mean T) per (node, channel). y: (k, N, C), temp: (k, N).

    The quadratic term soaks up slow curvature (e.g. creep starting inside the window)
    so it is not mistaken for a thermal response."""
    k, n, c = y.shape
    tm = np.nan_to_num(nanmedian(temp), nan=0.0)
    tc = np.nan_to_num(temp - tm)
    w = (~np.isnan(y)) & np.isfinite(temp)[..., None]
    wf = w.astype(float)
    tt = np.broadcast_to(t[:, None, None], (k, n, c))
    cols = np.stack([np.ones((k, n, c)), tt, tt * tt, np.broadcast_to(tc[..., None], (k, n, c))], axis=-1)
    xw = cols * wf[..., None]
    ata = np.einsum("knci,kncj->ncij", xw, cols)
    atb = np.einsum("knci,knc->nci", xw, np.nan_to_num(y))
    # Ridge on g only, so flat temperature leaves g at 0 instead of noise.
    ridge = np.zeros((4, 4))
    ridge[3, 3] = 1e-3 * k
    ata = ata + ridge + np.eye(4) * 1e-9
    sol = np.linalg.solve(ata, atb[..., None])[..., 0]
    g = sol[..., 3]
    return np.where(wf.sum(0) > 30, g, 0.0)


@dataclass
class Baseline:
    """Per-node reference captured over the first hour after install (or restart)."""

    level: np.ndarray        # (N, C) median of each channel
    temp_ref: np.ndarray     # (N,)
    vib: np.ndarray          # (N,) median vibration RMS
    noise: np.ndarray        # (N, 4) robust noise sigma of tilt_x, tilt_y, crack, ext
    ready: np.ndarray        # (N,) bool


def make_baseline(win: np.ndarray, min_rows: int) -> Baseline:
    n = win.shape[1]
    have = (~np.isnan(win[:, :, TX])).sum(0) >= min_rows
    level = nanmedian(win)
    d = np.diff(win[:, :, list(DEFORM)], axis=0)
    noise = MAD_K * nanmedian(np.abs(d - nanmedian(d))) / np.sqrt(2)
    vib = np.nan_to_num(level[:, VB], nan=5e-4)
    return Baseline(level=np.nan_to_num(level), temp_ref=np.nan_to_num(level[:, TP], nan=25.0),
                    vib=np.maximum(vib, 1e-4), noise=np.maximum(np.nan_to_num(noise, nan=1e-3), 1e-5),
                    ready=have if n else np.zeros(0, bool))


@dataclass
class Features:
    tilt_rate6: np.ndarray
    tilt_rate24: np.ndarray
    tilt_level: np.ndarray
    crack_rate6: np.ndarray
    crack_level: np.ndarray
    ext_rate6: np.ndarray
    ext_level: np.ndarray
    vib_ratio: np.ndarray
    vib_bursts: np.ndarray
    temp_excess: np.ndarray
    noise_ratio: np.ndarray
    batt_v: np.ndarray
    recent: np.ndarray            # readings in the last few periods (0 = silent)
    stuck: np.ndarray             # (N,) a deformation channel flat-lined
    spikes: np.ndarray            # (N,) isolated spikes in the last 6 h
    rate_noise6: np.ndarray       # (N, 4) noise floor of the 6 h slope per deformation channel
    extra: dict = field(default_factory=dict)

    IFOREST_KEYS = ("tilt_rate6", "tilt_rate24", "tilt_level", "crack_rate6", "crack_level",
                    "ext_rate6", "ext_level", "vib_ratio", "vib_bursts", "temp_excess", "noise_ratio")

    def matrix(self) -> np.ndarray:
        return np.stack([getattr(self, k) for k in self.IFOREST_KEYS], axis=1)


def compute(win: np.ndarray, period_s: float, base: Baseline, thermal_g: np.ndarray | None = None) -> Features:
    """win: (k, N, C) newest last, covering up to 24 h. Pass `thermal_g` to reuse a recent fit."""
    k, n, _ = win.shape
    per_day = 86400.0 / period_s
    t = (np.arange(k) - (k - 1)) / per_day  # days, 0 = now
    n6 = min(k, int(round(6 * 3600 / period_s)))
    n1 = min(k, int(round(3600 / period_s)))
    n_recent = min(k, 5)

    deform = win[:, :, list(DEFORM)]
    # The thermal coefficient needs most of a diurnal cycle; before that, no compensation.
    if thermal_g is not None:
        g = thermal_g
    elif k >= 0.8 * per_day:
        g = thermal_coefficients(t, deform, win[:, :, TP])
    else:
        g = np.zeros((n, 4))
    temp = np.nan_to_num(win[:, :, TP] - base.temp_ref, nan=0.0)
    comp = deform - g[None] * temp[..., None]                          # thermally compensated

    b24, _, _ = robust_slope(t, comp)
    b6, sig6, _ = robust_slope(t[-n6:], comp[-n6:])
    # Noise floor of a 6 h slope: sigma / (sqrt(n) * std(t)).
    st6 = np.std(t[-n6:]) if n6 > 1 else 1.0
    cnt6 = np.maximum((~np.isnan(comp[-n6:])).sum(0), 2)
    rate_noise6 = np.maximum(sig6, base.noise) / (np.sqrt(cnt6) * max(st6, 1e-9))

    recent = nanmedian(comp[-max(n1 // 2, 1):])                         # last ~30 min
    lvl = np.nan_to_num(recent - (base.level[:, list(DEFORM)] - 0.0))
    tilt_level = np.hypot(lvl[:, 0], lvl[:, 1])

    vib = win[-n1:, :, VB]
    vs = np.sort(vib, axis=0)
    vc = (~np.isnan(vib)).sum(0)
    vib_p90 = np.where(vc > 0, np.take_along_axis(vs, np.maximum(np.ceil(0.9 * vc).astype(int) - 1, 0)[None], 0)[0],
                       np.nan)
    vib_ratio = np.nan_to_num(vib_p90 / base.vib, nan=1.0)
    vib_bursts = np.nansum(vib > 4 * base.vib, axis=0).astype(float)

    node_temp = nanmedian(win[-n1:, :, TP])
    site_temp = nanmedian(node_temp[:, None], axis=0)[0] if np.isfinite(node_temp).any() else np.nan
    temp_excess = np.nan_to_num(node_temp - site_temp)

    noise_ratio = np.nan_to_num(np.max(sig6[:, :2] / base.noise[:, :2], axis=1), nan=1.0)

    # Stuck: a deformation channel that normally shows noise is exactly flat for 2 h.
    n2 = min(k, int(round(2 * 3600 / period_s)))
    seg = deform[-n2:]
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        ptp = np.nanmax(seg, axis=0) - np.nanmin(seg, axis=0)
    enough = (~np.isnan(seg)).sum(0) >= max(10, n2 // 3)
    stuck = ((ptp <= 1e-9) & enough).any(axis=1)

    # Spikes: a point far from the median of its 5-sample neighbourhood (a step is not a spike).
    s = deform[-n6:]
    if len(s) >= 5:
        pad = np.concatenate([s[:1], s[:1], s, s[-1:], s[-1:]])
        stack = np.stack([pad[i:i + len(s)] for i in range(5)])
        med5 = nanmedian(stack)
        dev = np.abs(np.nan_to_num(s - med5))
        spikes = (dev > 12 * base.noise[None] + 1e-9).sum(axis=(0, 2)).astype(float)
    else:
        spikes = np.zeros(n)

    batt = win[-n1:, :, BT]
    batt_v = np.nan_to_num(nanmedian(batt), nan=4.0)

    return Features(
        tilt_rate6=np.hypot(b6[:, 0], b6[:, 1]),
        tilt_rate24=np.hypot(b24[:, 0], b24[:, 1]),
        tilt_level=tilt_level,
        crack_rate6=b6[:, 2],
        crack_level=lvl[:, 2],
        ext_rate6=np.abs(b6[:, 3]),
        ext_level=np.abs(lvl[:, 3]),
        vib_ratio=vib_ratio,
        vib_bursts=vib_bursts,
        temp_excess=temp_excess,
        noise_ratio=noise_ratio,
        batt_v=batt_v,
        recent=(~np.isnan(win[-n_recent:, :, TX])).sum(0).astype(float),
        stuck=stuck,
        spikes=spikes,
        rate_noise6=rate_noise6,
        extra={"thermal_g": g, "comp": comp, "t": t, "b6": b6},
    )
