# SPDX-License-Identifier: MIT
"""Labelled runs for training and scoring: arrays plus ground truth.

Sources: W1's simulator (`achal_sim.simulate`), its JSONL exports
(data/samples/<scenario>.jsonl, data/runs/...), or the built-in surrogate.
Truth contract (W1): truth.hazard, truth.event, truth.fault, truth.onset_ts, truth.failure_ts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from achal_risk.store import CHANNELS


@dataclass
class Run:
    scenario: str
    seed: int
    source: str
    t_s: np.ndarray            # (T,) epoch seconds, regular cadence
    x: np.ndarray              # (T, N, C) NaN = missing
    node_ids: list[str]
    onset_s: np.ndarray        # (N,) first hazard time per node, inf if never a hazard
    failure_s: float | None    # scenario failure time (collapse, damage threshold), None if none
    fault: list[str | None]    # sensor fault per node

    @property
    def hazard_nodes(self) -> np.ndarray:
        return np.isfinite(self.onset_s)

    @property
    def first_onset_s(self) -> float:
        return float(self.onset_s.min()) if self.hazard_nodes.any() else float("inf")

    @property
    def days(self) -> float:
        return (self.t_s[-1] - self.t_s[0]) / 86400 if len(self.t_s) > 1 else 0.0


def _ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def from_records(records: Iterable[dict], node_ids: list[str], period_s: float, scenario: str = "?",
                 seed: int = -1, source: str = "records") -> Run:
    """Build a Run from telemetry dicts (JSON-ready, as W1 emits them)."""
    idx = {n: i for i, n in enumerate(node_ids)}
    rows: dict[int, np.ndarray] = {}
    n = len(node_ids)
    onset = np.full(n, np.inf)
    fault: list[str | None] = [None] * n
    failure = None
    t_min = None
    for r in records:
        i = idx.get(r.get("node"))
        if i is None:
            continue
        ts = _ts(r["ts"])
        k = int(round(ts / period_s))
        t_min = k if t_min is None else min(t_min, k)
        row = rows.setdefault(k, np.full((n, len(CHANNELS)), np.nan))
        row[i] = [np.nan if r.get(c) is None else float(r[c]) for c in CHANNELS]
        tr = r.get("truth") or {}
        if tr.get("hazard"):
            onset[i] = min(onset[i], _ts(tr.get("onset_ts")) or ts)
        if tr.get("fault") and fault[i] is None:
            fault[i] = tr["fault"]
        if failure is None and tr.get("failure_ts"):
            failure = _ts(tr["failure_ts"])
    if not rows:
        raise ValueError("no telemetry records")
    k0, k1 = min(rows), max(rows)
    x = np.full((k1 - k0 + 1, n, len(CHANNELS)), np.nan)
    for k, row in rows.items():
        x[k - k0] = row
    t = (np.arange(k0, k1 + 1)) * period_s
    return Run(scenario, seed, source, t, x, list(node_ids), onset, failure, fault)


def load_jsonl(path: str | Path, node_ids: list[str], period_s: float) -> list[Run]:
    """One file may hold several runs; they are split by (scenario, seed) if those keys exist."""
    groups: dict[tuple, list[dict]] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                key = (r.get("scenario") or (r.get("truth") or {}).get("scenario") or Path(path).stem,
                       r.get("seed", -1))
                groups.setdefault(key, []).append(r)
    return [from_records(recs, node_ids, period_s, scenario=k[0], seed=k[1], source=str(path))
            for k, recs in groups.items()]


def drop_packets(run: Run, loss: float, seed: int) -> Run:
    """Apply random packet loss (mesh stand-in when replaying loss-free simulator output)."""
    if loss <= 0:
        return run
    rng = np.random.default_rng(seed + 7919)
    x = run.x.copy()
    x[rng.random(x.shape[:2]) < loss] = np.nan
    return Run(run.scenario, run.seed, run.source, run.t_s, x, run.node_ids, run.onset_s, run.failure_s, run.fault)
