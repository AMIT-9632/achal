# SPDX-License-Identifier: MIT
"""Inverse-distance-weighted risk raster for the dashboard heatmap (kriging later).

Payload on achal/{site}/risk/grid follows schemas/risk-grid.v1.json: local metres,
row-major values, row 0 = southmost row at y0_m.
"""

from __future__ import annotations

import numpy as np


def idw_grid(xy: np.ndarray, values: np.ndarray, x0: float, y0: float, dx: float, dy: float,
             rows: int, cols: int, power: float = 2.0) -> np.ndarray:
    ok = ~np.isnan(values)
    if not ok.any():
        return np.zeros((rows, cols))
    xs = x0 + dx * np.arange(cols)
    ys = y0 + dy * np.arange(rows)
    gx, gy = np.meshgrid(xs, ys)
    d2 = (gx[..., None] - xy[ok, 0]) ** 2 + (gy[..., None] - xy[ok, 1]) ** 2
    w = 1.0 / np.maximum(d2, 1e-6) ** (power / 2)
    return (w * values[ok]).sum(-1) / w.sum(-1)
