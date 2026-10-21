# SPDX-License-Identifier: MIT
"""The layered risk engine.

Every `eval_period_s` of sensor time it scores all nodes at once:

  rules (rates + levels) ─┐
  Isolation Forest ───────┼─ evidence = max(rules, 0.8 x iforest, trend)
  inverse-velocity trend ─┘
        │
  neighbour consensus: evidence x (0.45 + 0.55 x c), c = min(1, (agreeing + s) / 2),
        │              s = 0.5 if two independent sensor groups agree on the node itself
  fault handling: faulty channels muted, lone single-channel signals marked suspect
        │
  hysteresis: up after `confirm_up` ticks, down one level after `confirm_down` ticks
        │
  node level -> site level -> alert on change
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from achal_risk import features as feat
from achal_risk.anomaly import AnomalyModel
from achal_risk.grid import idw_grid
from achal_risk.rules import BOUNDS, Rules
from achal_risk.store import CHANNELS, CR, EX, TX, TY, VB, Store
from achal_risk.ttf import inverse_velocity_ttf, trend_score

LEVELS = ("normal", "watch", "warning", "critical")
SUPPORT = 0.30  # evidence a neighbour needs to count as agreeing


def level_for(score: float) -> int:
    return sum(score >= b for b in BOUNDS)


@dataclass
class EngineConfig:
    eval_period_s: float = 600.0
    window_h: float = 24.0
    baseline_h: float = 1.0
    warmup_h: float = 6.0
    iforest_weight: float = 0.8
    use_iforest: bool = True
    use_consensus: bool = True
    use_hysteresis: bool = True
    use_trend: bool = True
    use_faults: bool = True
    stub: bool = False           # scaffold v0 behaviour, for the ablation baseline
    confirm_up: int = 2
    confirm_down: int = 18        # 3 h at the default cadence
    down_margin: float = 0.05
    silent_after: int = 5        # sample periods without data
    neighbour_radius: float = 1.5  # in grid spacings (8-neighbourhood)
    battery_low_v: float = 3.3
    ttf_after_h: float = 6.0


@dataclass
class NodeRisk:
    node: str
    score: float
    level: str
    components: dict[str, float]
    reasons: list[str]
    agreeing_neighbours: int
    ttf_hours: float | None


@dataclass
class EvalResult:
    ts_s: float
    scores: np.ndarray
    levels: np.ndarray            # int rank per node
    site_level: int
    nodes: list[NodeRisk]
    alert: dict | None
    compute_ms: float
    faulty: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))


# Scaffold thresholds, kept only to measure what the layered engine adds.
_STUB = {"tilt_deg": 0.05, "crack_mm": 1.0, "vib_rms_g": 0.02}


class RiskEngine:
    def __init__(self, node_ids: list[str], positions_m: np.ndarray, period_s: float, spacing_m: float,
                 model: AnomalyModel | None = None, rules: Rules | None = None,
                 cfg: EngineConfig | None = None) -> None:
        self.cfg = cfg or EngineConfig()
        self.node_ids = list(node_ids)
        self.idx = {n: i for i, n in enumerate(self.node_ids)}
        self.xy = np.asarray(positions_m, float)
        self.period_s = period_s
        n = len(node_ids)
        self.store = Store(n, period_s, self.cfg.window_h * 3600)
        self.model = model
        self.rules = rules or Rules()
        d = np.hypot(*(self.xy[:, None, :] - self.xy[None, :, :]).transpose(2, 0, 1))
        self.nbr = (d > 0) & (d <= self.cfg.neighbour_radius * spacing_m + 1e-6)
        self.base: feat.Baseline | None = None
        self.counts = np.zeros(n, int)
        self.first_idx: int | None = None
        self.level = np.zeros(n, int)
        self.up_count = np.zeros(n, int)
        self.down_count = np.zeros(n, int)
        self.last_score = np.zeros(n)
        self.moving_ticks = np.zeros(n, int)
        self.tf_hist: list[tuple[float, np.ndarray]] = []  # (eval time, predicted failure time per node)
        self.site_level = 0
        self.next_eval: float | None = None
        self.last_features: feat.Features | None = None
        self._g: np.ndarray | None = None
        self._g_age = 0

    @classmethod
    def for_site(cls, site, **kw) -> RiskEngine:
        ids = site.node_ids()
        pos = np.array([[p.x, p.y] for p in (site.node_position_m(i) for i in ids)])
        return cls(ids, pos, site.mesh.sample_period_s, site.mesh.spacing_m, **kw)

    # ---------------------------------------------------------------- ingest
    def ingest(self, node: str, ts_s: float, reading: dict) -> None:
        i = self.idx.get(node)
        if i is None:
            return
        vals = np.array([reading.get(c, np.nan) if reading.get(c) is not None else np.nan for c in CHANNELS])
        if self.store.put(i, ts_s, vals):
            self.counts[i] += 1
            if self.first_idx is None:
                self.first_idx = self.store.index(ts_s)

    def ingest_row(self, ts_s: float, rows: np.ndarray) -> None:
        self.store.put_row(ts_s, rows)
        self.counts += ~np.isnan(rows[:, TX])
        if self.first_idx is None:
            self.first_idx = self.store.index(ts_s)

    def due(self, ts_s: float) -> bool:
        p = self.cfg.eval_period_s
        if self.next_eval is None:
            self.next_eval = math.ceil(ts_s / p) * p
        return ts_s >= self.next_eval

    def maybe_evaluate(self, ts_s: float) -> EvalResult | None:
        if not self.due(ts_s):
            return None
        p = self.cfg.eval_period_s
        self.next_eval = (math.floor(ts_s / p) + 1) * p
        return self.evaluate(ts_s)

    # ---------------------------------------------------------------- evaluate
    def _ensure_baseline(self, win: np.ndarray) -> np.ndarray:
        n = len(self.node_ids)
        if self.base is None:
            self.base = feat.Baseline(level=np.zeros((n, len(CHANNELS))), temp_ref=np.full(n, 25.0),
                                      vib=np.full(n, 5e-4), noise=np.full((n, 4), 1e-3), ready=np.zeros(n, bool))
        rows = max(6, int(round(self.cfg.baseline_h * 3600 / self.period_s)))
        todo = ~self.base.ready & (self.counts >= rows)
        if todo.any():
            b = feat.make_baseline(win, rows)
            for name in ("level", "temp_ref", "vib", "noise"):
                getattr(self.base, name)[todo] = getattr(b, name)[todo]
            self.base.ready[todo] = True
        return self.base.ready

    def evaluate(self, ts_s: float) -> EvalResult:
        t0 = time.perf_counter()
        cfg = self.cfg
        n = len(self.node_ids)
        win = self.store.window(int(round(cfg.window_h * 3600 / self.period_s)))
        ready = self._ensure_baseline(win)
        if cfg.stub:
            return self._stub(ts_s, win, t0)

        # Thermal coefficients are sensor properties: refresh them hourly, and only on
        # nodes that are quiet, so real ground movement cannot leak into them.
        data_rows = self.store.latest - (self.first_idx or 0) + 1
        if self._g_age <= 0 and data_rows >= 86400 / self.period_s:
            g_new = feat.thermal_coefficients(*self._thermal_inputs(win))
            quiet = self.last_score < SUPPORT
            if self._g is None:
                self._g = g_new  # first day: every node (the quadratic trend term absorbs slow movement)
            else:
                self._g[quiet] = g_new[quiet]
            self._g_age = int(3600 / cfg.eval_period_s)
        self._g_age -= 1
        f = feat.compute(win, self.period_s, self.base,
                         thermal_g=self._g if self._g is not None else np.zeros((n, 4)))
        self.last_features = f
        age_h = (self.store.latest - (self.first_idx or 0)) * self.period_s / 3600
        if age_h < cfg.warmup_h:  # rates are meaningless on a few minutes of data
            for k in ("tilt_rate6", "tilt_rate24", "crack_rate6", "ext_rate6"):
                setattr(f, k, np.zeros(n))
        silent = f.recent == 0
        faulty = (f.stuck | (f.spikes >= 3) | (f.batt_v < cfg.battery_low_v)) if cfg.use_faults else np.zeros(n, bool)

        rules, groups, reason_scores = self.rules.score(f, faulty)
        if self._g is None:
            # Until thermal coefficients are known (a day of data, or commissioning), daily
            # temperature swings look like movement on every node at once, which consensus
            # cannot reject. Rate-driven evidence is held below Warning until then.
            level_only = self.rules.score(f, faulty, rates=False)[0]
            rules = np.maximum(level_only, np.minimum(rules, BOUNDS[1] - 0.01))
        if self.model is not None and cfg.use_iforest:
            a = self.model.score(f.matrix())
            a = np.where(faulty, np.minimum(a, 0.3), a)
        else:
            a = np.zeros(n)

        ttf = np.full(n, np.nan)
        if cfg.use_trend and age_h >= cfg.warmup_h:
            # Only nodes already showing movement get a time-to-failure.
            # Only nodes whose movement is established (rules above SUPPORT for `ttf_after_h`)
            # get a time-to-failure: Fukuzono's law holds in the creep phase, not at its onset.
            self.moving_ticks = np.where(rules >= SUPPORT, self.moving_ticks + 1, 0)
            moving = ~faulty & (self.moving_ticks * cfg.eval_period_s >= cfg.ttf_after_h * 3600)
            if moving.any():
                ttf[moving] = self._ttf(f, moving)
            ttf = self._neighbourhood_ttf(self._stable_ttf(ts_s, ttf))
        trend = trend_score(ttf)

        evidence = np.maximum.reduce([rules, cfg.iforest_weight * a, trend])
        evidence = np.where(ready, evidence, 0.0)
        healthy = ready & ~silent & ~faulty
        support = (evidence >= SUPPORT) & healthy
        agreeing = (self.nbr & support[None, :]).sum(1)
        n_groups = sum((groups[g] >= SUPPORT).astype(int) for g in ("tilt", "strain", "heat")) + \
            ((groups["vib"] >= SUPPORT) & (np.maximum(groups["tilt"], groups["strain"]) >= SUPPORT)).astype(int)
        # Two independent sensor groups on one node count as half a neighbour: enough to
        # lift one agreeing neighbour to full support, never enough to warn on its own.
        c = np.minimum(1.0, (agreeing + 0.5 * (n_groups >= 2)) / 2.0)
        if cfg.use_consensus:
            fused = evidence * (0.45 + 0.55 * c)
        else:
            fused = evidence.copy()
        lone = (n_groups <= 1) & (agreeing == 0) & (evidence >= BOUNDS[0])
        suspect = faulty | (lone & cfg.use_consensus & cfg.use_faults)

        # A silent node keeps its last score; next to a Warning it may have been destroyed.
        nb_warn = (self.nbr & (self.level[None, :] >= 2)).any(1)
        fused = np.where(silent, np.where(nb_warn, np.maximum(self.last_score, BOUNDS[1]), self.last_score), fused)
        fused = np.clip(np.where(ready | silent, fused, 0.0), 0, 1)
        self.last_score = fused

        target = np.array([level_for(s) for s in fused])
        self._apply_hysteresis(target, fused)

        nodes = []
        for i, nid in enumerate(self.node_ids):
            r = []
            if fused[i] >= BOUNDS[0] or self.level[i] > 0:
                r += [code for code, s in reason_scores.items() if s[i] >= BOUNDS[0]]
                if a[i] >= BOUNDS[0]:
                    r.append("iforest_anomaly")
                if agreeing[i] >= 2 and cfg.use_consensus:
                    r.append("neighbour_consensus")
                if trend[i] >= BOUNDS[1]:
                    r.append("ttf_short")
            if silent[i]:
                r.append("node_silent")
            if suspect[i]:
                r.append("sensor_fault_suspected")
            nodes.append(NodeRisk(
                node=nid, score=round(float(fused[i]), 4), level=LEVELS[self.level[i]],
                components={"rules": round(float(rules[i]), 4), "iforest": round(float(a[i]), 4),
                            "trend": round(float(trend[i]), 4), "consensus": round(float(c[i]), 4),
                            "fused": round(float(fused[i]), 4)},
                reasons=r, agreeing_neighbours=int(agreeing[i]),
                ttf_hours=None if np.isnan(ttf[i]) else round(float(ttf[i]), 2)))
        alert = self._site_alert(ts_s, nodes, ttf)
        return EvalResult(ts_s, fused, self.level.copy(), self.site_level, nodes, alert,
                          (time.perf_counter() - t0) * 1000, faulty)

    def _neighbourhood_ttf(self, ttf: np.ndarray) -> np.ndarray:
        """A failing pillar moves the ground over several nodes, so a time-to-failure must be
        shared: use the median over the node and its neighbours, needing two estimates."""
        out = np.full(len(ttf), np.nan)
        fin = np.isfinite(ttf)
        if fin.sum() < 2:
            return out
        for i in np.nonzero(fin | (self.nbr & fin[None, :]).any(1))[0]:
            vals = ttf[(self.nbr[i] | (np.arange(len(ttf)) == i)) & fin]
            if len(vals) >= 2:
                out[i] = float(np.median(vals))
        return out

    def _stable_ttf(self, ts_s: float, ttf: np.ndarray) -> np.ndarray:
        """Report a time-to-failure only if an estimate made 1-3 h earlier pointed to the same
        failure time (within 30%, at least 30 min). Spurious fits that always say
        "failure in a moment" slide forward with the clock and never agree with themselves."""
        tf_abs = ts_s + ttf * 3600
        self.tf_hist.append((ts_s, tf_abs))
        self.tf_hist = [(t, v) for t, v in self.tf_hist if ts_s - t <= 3 * 3600]
        tol = np.maximum(1800.0, 0.3 * ttf * 3600)
        agree = np.zeros(len(ttf), bool)
        for t, v in self.tf_hist:
            if ts_s - t >= 3600:
                agree |= np.abs(v - tf_abs) <= tol
        return np.where(agree, ttf, np.nan)

    def commission(self, quiet_window: np.ndarray) -> None:
        """Learn thermal coefficients from a stretch of quiet data recorded at install
        (rows, nodes, channels, newest last; ideally a day or more)."""
        self._g = feat.thermal_coefficients(*self._thermal_inputs(quiet_window))
        self._g_age = int(3600 / self.cfg.eval_period_s)

    def _thermal_inputs(self, win: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        k = len(win)
        t = (np.arange(k) - (k - 1)) * self.period_s / 86400
        return t, win[:, :, list(feat.DEFORM)], win[:, :, feat.TP]

    def _ttf(self, f: feat.Features, sel: np.ndarray) -> np.ndarray:
        """Time to failure from the displacement proxies, each projected on its current
        direction of motion (a magnitude like |tilt| is biased by noise near zero and
        would fake acceleration)."""
        comp, t, b6 = f.extra["comp"][:, sel], f.extra["t"], f.extra["b6"][sel]
        lvl = self.base.level[sel]
        bt = np.hypot(b6[:, 0], b6[:, 1])
        ux, uy = b6[:, 0] / np.maximum(bt, 1e-12), b6[:, 1] / np.maximum(bt, 1e-12)
        tilt = (comp[:, :, 0] - lvl[:, TX]) * ux + (comp[:, :, 1] - lvl[:, TY]) * uy
        crack = (comp[:, :, 2] - lvl[:, CR]) * np.sign(b6[:, 2])
        ext = (comp[:, :, 3] - lvl[:, EX]) * np.sign(b6[:, 3])
        noise = self.base.noise[sel]
        best = np.full(int(sel.sum()), np.nan)
        for series, nr in ((tilt, np.hypot(noise[:, 0], noise[:, 1])), (crack, noise[:, 2]), (ext, noise[:, 3])):
            h, _ = inverse_velocity_ttf(t, series, nr)
            best = np.fmin(best, h)
        return best

    def _apply_hysteresis(self, target: np.ndarray, fused: np.ndarray) -> None:
        cfg = self.cfg
        if not cfg.use_hysteresis:
            self.level = target
            return
        up = target > self.level
        self.up_count = np.where(up, self.up_count + 1, 0)
        go_up = up & (self.up_count >= cfg.confirm_up)
        self.level = np.where(go_up, target, self.level)
        self.up_count[go_up] = 0
        lower = np.array([BOUNDS[lv - 1] if lv > 0 else 0.0 for lv in self.level])
        below = (self.level > 0) & (fused < lower - cfg.down_margin)
        self.down_count = np.where(below, self.down_count + 1, 0)
        go_down = below & (self.down_count >= cfg.confirm_down)
        self.level = np.where(go_down, self.level - 1, self.level)
        self.down_count[go_down] = 0

    def _site_alert(self, ts_s: float, nodes: list[NodeRisk], ttf: np.ndarray) -> dict | None:
        new = int(self.level.max()) if len(self.level) else 0
        if new == self.site_level:
            return None
        prev, self.site_level = self.site_level, new
        hot = [i for i in range(len(nodes)) if self.level[i] == new] if new > 0 else []
        reasons = sorted({r for i in hot for r in nodes[i].reasons}) or ["level_change"]
        hot_ttf = ttf[hot] if hot else np.array([])
        finite = hot_ttf[~np.isnan(hot_ttf)] if len(hot_ttf) else hot_ttf
        finite = np.array([np.median(finite)]) if len(finite) else finite
        return {
            "ts_s": ts_s, "level": LEVELS[new], "previous_level": LEVELS[prev],
            "nodes": [self.node_ids[i] for i in hot], "reasons": reasons,
            "ttf_hours": round(float(finite.min()), 2) if len(finite) else None,
            "centroid_m": self.xy[hot].mean(0).tolist() if hot else None,
            "siren": new >= 2,
        }

    def _stub(self, ts_s: float, win: np.ndarray, t0: float) -> EvalResult:
        last = win[-1]
        tilt = np.nan_to_num(np.maximum(np.abs(last[:, TX]), np.abs(last[:, TY])))
        parts = np.stack([tilt / _STUB["tilt_deg"], np.nan_to_num(np.abs(last[:, CR])) / _STUB["crack_mm"],
                          np.nan_to_num(last[:, VB]) / _STUB["vib_rms_g"]])
        s = np.minimum(1.0, parts.max(0) / 2)
        self.level = np.array([level_for(x) for x in s])
        nodes = [NodeRisk(nid, float(s[i]), LEVELS[self.level[i]], {"rules": float(s[i])}, [], 0, None)
                 for i, nid in enumerate(self.node_ids)]
        alert = self._site_alert(ts_s, nodes, np.full(len(s), np.nan))
        return EvalResult(ts_s, s, self.level.copy(), self.site_level, nodes, alert,
                          (time.perf_counter() - t0) * 1000, np.zeros(len(s), bool))

    # ---------------------------------------------------------------- grid
    def grid(self, scores: np.ndarray, x0: float = -40, y0: float = -40, d: float = 5.0,
             extent: float = 180.0) -> dict:
        rows = cols = int(extent / d) + 1
        g = idw_grid(self.xy, scores, x0, y0, d, d, rows, cols)
        return {"x0_m": x0, "y0_m": y0, "dx_m": d, "dy_m": d, "rows": rows, "cols": cols,
                "values": [round(float(v), 3) for v in g.ravel()], "method": "idw"}

