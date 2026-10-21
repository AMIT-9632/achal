# SPDX-License-Identifier: MIT
"""Isolation Forest over node feature vectors, trained on normal-scenario data only.

The raw anomaly score is rescaled so the 99.9th percentile of training data maps
to 0.35 (the Watch boundary): normal noise stays below Watch by construction,
and only behaviour unlike anything seen in normal ground climbs higher.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest


@dataclass
class AnomalyModel:
    forest: IsolationForest
    q50: float
    q999: float
    train_rows: int
    train_features: np.ndarray | None = None  # kept for rule noise-floor calibration

    @classmethod
    def fit(cls, x: np.ndarray, seed: int = 0, n_estimators: int = 150) -> AnomalyModel:
        x = np.nan_to_num(x)
        forest = IsolationForest(n_estimators=n_estimators, max_samples=min(512, len(x)),
                                 contamination="auto", random_state=seed).fit(x)
        s = -forest.score_samples(x)
        return cls(forest, float(np.percentile(s, 50)), float(np.percentile(s, 99.9)), len(x), x)

    def score(self, x: np.ndarray) -> np.ndarray:
        s = -self.forest.score_samples(np.nan_to_num(x))
        return np.clip(0.35 * (s - self.q50) / max(self.q999 - self.q50, 1e-6), 0.0, 1.0)
