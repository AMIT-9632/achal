# SPDX-License-Identifier: MIT
"""Inverse-velocity time-to-failure (Fukuzono 1985; Voight 1988).

Before a brittle collapse, displacement rate v grows roughly as 1/(t_f - t), so
1/v falls linearly and reaches zero at the failure time t_f. Differentiating
noisy data to get 1/v is fragile, so we fit the integrated form of the same law,

    d(t) = a - A ln(t_f - t),   A > 0,

directly to a thermally compensated displacement proxy (10-min means over the last
24 h), scanning candidate t_f and solving (a, A) by least squares for each. The
fit uses only the data since the motion started (found as the best flat-then-straight
hinge), needs at least 4 h of it, must explain at least half of what a constant
velocity leaves unexplained, must rise well above noise, and t_f may lie no further
ahead than twice the span of the motion seen so far.
"""

from __future__ import annotations

import numpy as np


def _sse_many(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares fit y = a + b*x for many regressors at once.
    x: (M, k) candidate regressors, y/w: (k, N). Returns (sse, b), each (M, N)."""
    xm = x[:, :, None]
    sw = w.sum(0)[None]
    sx = (w[None] * xm).sum(1)
    sy = (w * y).sum(0)[None]
    sxx = (w[None] * xm * xm).sum(1)
    sxy = (w[None] * xm * y[None]).sum(1)
    den = sw * sxx - sx * sx
    with np.errstate(invalid="ignore", divide="ignore"):
        b = np.where(den > 1e-18, (sw * sxy - sx * sy) / den, 0.0)
        a = np.where(sw > 0, (sy - b * sx) / np.maximum(sw, 1e-12), 0.0)
    resid = y[None] - (a[:, None, :] + b[:, None, :] * xm)
    return (w[None] * resid * resid).sum(1), b


def _best_break(tb: np.ndarray, y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Per node, the t0 of the best fit y = a + b*t + c*max(0, t - t0). y/w: (k, N)."""
    t0s = tb[2:-3]
    if len(t0s) == 0:
        return np.full(y.shape[1], tb[0])
    h = np.maximum(tb[None] - t0s[:, None], 0.0)                                # (M, k)
    m, n = len(t0s), y.shape[1]
    wy = w * y
    s1, st, stt = w.sum(0), tb @ w, (tb * tb) @ w                               # (N,)
    sh, sth, shh = h @ w, (h * tb) @ w, (h * h) @ w                              # (M, N)
    ata = np.empty((m, n, 3, 3))
    ata[..., 0, 0], ata[..., 0, 1], ata[..., 1, 1] = s1, st, stt
    ata[..., 0, 2], ata[..., 1, 2], ata[..., 2, 2] = sh, sth, shh
    ata[..., 1, 0], ata[..., 2, 0], ata[..., 2, 1] = ata[..., 0, 1], ata[..., 0, 2], ata[..., 1, 2]
    atb = np.empty((m, n, 3))
    atb[..., 0], atb[..., 1], atb[..., 2] = wy.sum(0), tb @ wy, h @ wy
    coef = np.linalg.solve(ata + np.eye(3) * 1e-12, atb[..., None])[..., 0]    # (M, N, 3)
    sse = (wy * y).sum(0)[None] - (coef * atb).sum(-1)                          # (M, N)
    return t0s[sse.argmin(0)]


def inverse_velocity_ttf(t_days: np.ndarray, series: np.ndarray, noise_sigma: np.ndarray,
                         bin_minutes: float = 10.0, min_gain: float = 0.5, min_snr: float = 10.0,
                         min_hours: float = 4.0, max_hours: float = 720.0) -> tuple[np.ndarray, np.ndarray]:
    """series: (k, N) displacement proxy (grows with deformation); noise_sigma: (N,) per-sample noise.
    Returns (ttf_hours, gain) per node, NaN where no accelerating fit is accepted."""
    k, n = series.shape
    out = np.full(n, np.nan)
    gain_out = np.full(n, np.nan)
    if k < 10 or n == 0:
        return out, gain_out
    period_days = (t_days[-1] - t_days[0]) / (k - 1)
    b = max(int(round(bin_minutes / 1440 / period_days)), 1)
    nb = k // b
    if nb < 12:
        return out, gain_out
    y = series[k - nb * b:].reshape(nb, b, n)
    cnt = (~np.isnan(y)).sum(1)
    yb = np.where(cnt > 0, np.nansum(y, 1) / np.maximum(cnt, 1), np.nan)
    tb = t_days[k - nb * b:].reshape(nb, b).mean(1)
    w = (~np.isnan(yb)).astype(float)
    y0 = np.nan_to_num(yb)
    span_days = tb[-1] - tb[0]
    min_bins = int(min_hours * 60 / bin_minutes)

    # Where did the current phase of motion start? Fit a broken line (velocity changes once,
    # at t0) for every candidate t0 and keep the best. Fukuzono's law describes the creep
    # phase, so everything below is fitted on the data after that break: a velocity that
    # simply stepped up (or creep that only just began) is then not mistaken for acceleration.
    start = _best_break(tb, y0, w)
    wp = w * (tb[:, None] >= start[None])
    n_post = wp.sum(0)

    sse_lin, slope = _sse_many(tb[None], y0, wp)
    sse_lin, slope = sse_lin[0], slope[0]
    post_span = np.maximum(tb[-1] - start, 1e-9)
    rise = slope * post_span
    mean_post = (wp * y0).sum(0) / np.maximum(n_post, 1)
    sse_tot = (wp * (y0 - mean_post) ** 2).sum(0)
    sigma_bin = noise_sigma / np.sqrt(b)

    horizon = min(max_hours / 24, 2 * span_days)
    tfs = np.geomspace(0.25 / 24, horizon, 40)
    logs = -np.log(tfs[:, None] - tb[None] + 1e-9)
    sse_l, amp = _sse_many(logs, y0, wp)
    sse_l = np.where(amp > 0, sse_l, np.inf)
    best = sse_l.argmin(0)
    best_sse = sse_l[best, np.arange(n)]
    best_tf = tfs[best]
    gain = 1 - best_sse / np.maximum(sse_lin, 1e-18)
    ok = (np.isfinite(best_sse) & (gain >= min_gain) & (rise > min_snr * sigma_bin)
          & (best_sse < 0.2 * sse_tot) & (n_post >= min_bins)
          & (best_tf <= 2 * post_span))
    out[ok] = best_tf[ok] * 24
    gain_out[ok] = gain[ok]
    return out, gain_out


def trend_score(ttf_hours: np.ndarray) -> np.ndarray:
    """Map time-to-failure to a 0-1 score: <= 6 h critical, 24 h warning, 72 h watch."""
    h = np.where(np.isnan(ttf_hours), np.inf, ttf_hours)
    return np.interp(np.log10(np.clip(h, 0.1, 1e6)), np.log10([2, 6, 24, 72, 168]),
                     [0.95, 0.85, 0.6, 0.35, 0.0], left=0.95, right=0.0)
