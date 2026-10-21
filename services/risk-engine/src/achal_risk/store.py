# SPDX-License-Identifier: MIT
"""Fixed-cadence ring buffer of recent readings, one row per sample period.

Readings are slotted by their own timestamp, so packets replayed late from the
gateway buffer land where they belong. Missing samples stay NaN.
"""

from __future__ import annotations

import numpy as np

# Channel order used everywhere in the engine.
CHANNELS = ("tilt_x_deg", "tilt_y_deg", "crack_mm", "ext_mm", "vib_rms_g", "temp_c", "batt_v")
TX, TY, CR, EX, VB, TP, BT = range(len(CHANNELS))


class Store:
    def __init__(self, n_nodes: int, period_s: float, window_s: float) -> None:
        self.period_s = period_s
        self.size = int(round(window_s / period_s)) + 1
        self.buf = np.full((self.size, n_nodes, len(CHANNELS)), np.nan)
        self.latest: int | None = None  # absolute sample index of the newest row

    def index(self, ts_s: float) -> int:
        return int(round(ts_s / self.period_s))

    def _advance(self, idx: int) -> None:
        if self.latest is None:
            self.latest = idx - 1
        if idx <= self.latest:
            return
        n_new = min(idx - self.latest, self.size)
        for k in range(idx - n_new + 1, idx + 1):
            self.buf[k % self.size] = np.nan
        self.latest = idx

    def put(self, node: int, ts_s: float, values: np.ndarray) -> bool:
        """Store one node's reading. Returns False if it is too old to keep."""
        idx = self.index(ts_s)
        self._advance(idx)
        if idx <= self.latest - self.size:
            return False
        self.buf[idx % self.size, node] = values
        return True

    def put_row(self, ts_s: float, rows: np.ndarray) -> None:
        """Store readings for all nodes at one time (NaN rows = missing)."""
        idx = self.index(ts_s)
        self._advance(idx)
        if idx > self.latest - self.size:
            slot = self.buf[idx % self.size]
            ok = ~np.isnan(rows).all(axis=1)
            slot[ok] = rows[ok]

    def window(self, n_rows: int) -> np.ndarray:
        """Last n_rows samples in time order, shape (n_rows, nodes, channels)."""
        n_rows = min(n_rows, self.size)
        if self.latest is None:
            return self.buf[:0]
        idx = np.arange(self.latest - n_rows + 1, self.latest + 1) % self.size
        return self.buf[idx]
