# SPDX-License-Identifier: MIT
"""Inverse-velocity time-to-failure (Fukuzono 1985; Voight 1988).

Before a brittle collapse, displacement rate v grows roughly as 1/(t_f - t), so
1/v falls linearly and reaches zero at the failure time t_f. Differentiating
noisy data to get 1/v is fragile, so we fit the integrated form of the same law,

    d(t) = a - A ln(t_f - t),   A > 0,

directly to a thermally compensated displacement proxy (10-min means over the last
24 h), scanning candidate t_f and solving (a, A) by least squares for each. A fit
is accepted only if it explains at least half of what the best constant-velocity
model leaves unexplained (a straight line, or flat-then-straight for creep that
started inside the window), the motion is well above noise, and t_f lies no further ahead
than twice the span of the data.
"""

from __future__ import annotations

import numpy as np

from achal_risk.features import _wls_line


def inverse_velocity_ttf(t_days: np.ndarray, series: np.ndarray, noise_sigma: np.ndarray,
                         bin_minutes: float = 10.0, min_gain: float = 0.5, min_snr: float = 10.0,
                         max_hours: float = 720.0) -> tuple[np.ndarray, np.ndarray]:
    """series: (k, N) displacement proxy (grows with deformation); noise_sigma: (N,) per-sample noise.
    Returns (ttf_hours, gain) per node, NaN where no accelerating fit is accepted."""
    k, n = series.shape
    out = np.full(n, np.nan)
    gain_out = np.full(n, np.nan)
    if k < 10:
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

    a, bl = _wls_line(tb, y0, w)
    sse_lin = (w * (y0 - (a + bl * tb[:, None])) ** 2).sum(0)
    # Constant velocity that started inside the window (a hinge) is not acceleration.
    for t0 in tb[1:-3:max(nb // 24, 1)]:
        x = np.maximum(tb - t0, 0.0)
        ai, bi = _wls_line(x, y0, w)
        sse_lin = np.minimum(sse_lin, (w * (y0 - (ai + bi * x[:, None])) ** 2).sum(0))
    sse_tot = (w * (y0 - (w * y0).sum(0) / np.maximum(w.sum(0), 1)) ** 2).sum(0)
    rise = (a + bl * tb[-1]) - (a + bl * tb[0])
    sigma_bin = noise_sigma / np.sqrt(b)

    best_sse = np.full(n, np.inf)
    best_tf = np.full(n, np.nan)
    horizon = min(max_hours / 24, 2 * span_days)
    for tf in np.geomspace(0.25 / 24, horizon, 40):
        x = -np.log(tf - tb + 1e-9)
        ai, amp = _wls_line(x, y0, w)
        sse = (w * (y0 - (ai + amp * x[:, None])) ** 2).sum(0)
        better = (amp > 0) & (sse < best_sse)
        best_sse = np.where(better, sse, best_sse)
        best_tf = np.where(better, tf, best_tf)
    gain = 1 - best_sse / np.maximum(sse_lin, 1e-18)
    ok = (np.isfinite(best_tf) & (gain >= min_gain) & (rise > min_snr * sigma_bin)
          & (best_sse < 0.2 * sse_tot))
    out[ok] = best_tf[ok] * 24
    gain_out[ok] = gain[ok]
    return out, gain_out


def trend_score(ttf_hours: np.ndarray) -> np.ndarray:
    """Map time-to-failure to a 0-1 score: <= 6 h critical, 24 h warning, 72 h watch."""
    h = np.where(np.isnan(ttf_hours), np.inf, ttf_hours)
    return np.interp(np.log10(np.clip(h, 0.1, 1e6)), np.log10([2, 6, 24, 72, 168]),
                     [0.95, 0.85, 0.6, 0.35, 0.0], left=0.95, right=0.0)
