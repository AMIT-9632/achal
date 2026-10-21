# SPDX-License-Identifier: MIT
"""KPI harness: replay labelled scenarios through the risk engine and score it against truth.

    python -m achal_risk.kpi --seeds 20 --out reports/kpi          # W1 simulator (default)
    python -m achal_risk.kpi --source surrogate --seeds 3           # built-in generator, quick check

For every scenario x seed it trains nothing on the evaluation data: the Isolation Forest
and the rule noise floors are fitted on separate normal-scenario seeds (TRAIN_SEEDS).
Each run is scored by several engine configurations (an ablation), from the v0 scaffold
thresholds up to the full layered engine, so the report shows what each layer buys.

KPI definitions (see docs/kpis.md):
- recall: share of hazard runs where a hazard node reached Warning after onset and
  before failure_ts (for runs with a failure time) or before the run ended.
- lead time: failure_ts minus the first such Warning (also reported for Watch).
- false alarms per node-month: node Warning episodes that start where no hazard exists
  (in normal/faults runs, and before onset in hazard runs) / node-months observed.
- false sirens per month: site-level escalations to Warning (sirens sound) outside hazards.
- time-to-failure error: |ttf estimate - true time left| on hazard nodes in the last 12 h.
- fault flagging: share of injected sensor faults marked sensor_fault_suspected.
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from achal_risk.anomaly import AnomalyModel
from achal_risk.data import Run, drop_packets, from_records
from achal_risk.engine import EngineConfig, RiskEngine
from achal_risk.features import Features
from achal_risk.rules import Rules

SCENARIOS = ("normal", "trough", "pillar_collapse", "faults", "fire")
HAZARD_SCENARIOS = ("trough", "pillar_collapse", "fire")
# Run lengths for the harness (hours). Normal and faults run long to measure false-alarm rates.
HOURS = {"normal": 336.0, "faults": 168.0, "trough": 72.0, "pillar_collapse": 48.0, "fire": 96.0}
TRAIN_SEEDS = (1001, 1002, 1003, 1004)
COMMISSION_H = 26.0

CONFIGS: dict[str, EngineConfig] = {
    "v0_stub": EngineConfig(stub=True),
    "rules": EngineConfig(use_iforest=False, use_consensus=False, use_hysteresis=False, use_trend=False,
                          use_faults=False),
    "rules_iforest": EngineConfig(use_consensus=False, use_hysteresis=False, use_trend=False, use_faults=False),
    "plus_consensus": EngineConfig(use_hysteresis=False, use_trend=False),
    "full": EngineConfig(),
}


# ------------------------------------------------------------------ data sources
def _sim_path() -> None:
    here = Path(__file__).resolve()
    sim_src = here.parents[3] / "field-sim" / "src"
    if sim_src.exists() and str(sim_src) not in sys.path:
        sys.path.insert(0, str(sim_src))


def make_run(source: str, site, scenario: str, seed: int, hours: float | None = None) -> Run:
    if source == "surrogate":
        from achal_risk.surrogate import generate
        return generate(site, scenario, seed, days=(hours / 24) if hours else None)
    _sim_path()
    from achal_sim import simulate  # W1 field simulator

    recs = simulate(scenario, seed=seed, hours=hours, site=site)
    return from_records(recs, site.node_ids(), site.mesh.sample_period_s, scenario, seed, "achal_sim")


def commission_window(source: str, site, seed: int) -> np.ndarray:
    """A day of quiet data from the same sensors (same seed), as recorded at install."""
    run = make_run(source, site, "normal", seed, hours=COMMISSION_H)
    return run.x[-int(24 * 3600 / site.mesh.sample_period_s):]


# ------------------------------------------------------------------ replay
def replay(run: Run, site, cfg: EngineConfig, model: AnomalyModel | None, rules: Rules | None,
           commission: np.ndarray | None = None, keep_features: bool = False) -> dict:
    eng = RiskEngine.for_site(site, model=model, rules=rules, cfg=cfg)
    if commission is not None:
        eng.commission(commission)
    ts, levels, scores, ttf, suspect, ms, site_lv, feats = [], [], [], [], [], [], [], []
    for k in range(len(run.t_s)):
        eng.ingest_row(run.t_s[k], run.x[k])
        r = eng.maybe_evaluate(run.t_s[k])
        if r is None:
            continue
        ts.append(r.ts_s)
        levels.append(r.levels)
        scores.append(r.scores)
        ttf.append([np.nan if n.ttf_hours is None else n.ttf_hours for n in r.nodes])
        suspect.append([("sensor_fault_suspected" in n.reasons) for n in r.nodes])
        ms.append(r.compute_ms)
        site_lv.append(r.site_level)
        if keep_features and eng.last_features is not None:
            age_h = (r.ts_s - run.t_s[0]) / 3600
            if age_h >= cfg.warmup_h:
                feats.append(eng.last_features.matrix())
    return {"t": np.array(ts), "levels": np.array(levels), "scores": np.array(scores), "ttf": np.array(ttf),
            "suspect": np.array(suspect), "ms": np.array(ms), "site": np.array(site_lv),
            "features": np.concatenate(feats) if feats else None}


def train(source: str, site, seeds=TRAIN_SEEDS, hours: float = 72.0, log=print) -> tuple[AnomalyModel, Rules]:
    """Fit the Isolation Forest and the rule noise floors on normal-scenario seeds only."""
    feats = []
    cfg = EngineConfig(use_iforest=False, use_trend=False)
    for s in seeds:
        run = make_run(source, site, "normal", s, hours=hours)
        out = replay(run, site, cfg, None, None, commission_window(source, site, s), keep_features=True)
        feats.append(out["features"])
        log(f"  train seed {s}: {len(out['features'])} feature rows")
    x = np.concatenate(feats)
    model = AnomalyModel.fit(x, seed=0)
    rules = Rules()
    rules.calibrate_floor(x, Features.IFOREST_KEYS)
    return model, rules


# ------------------------------------------------------------------ scoring
def score_run(run: Run, out: dict, spacing_m: float) -> dict:
    t, lv = out["t"], out["levels"]
    n = lv.shape[1]
    hazard = run.hazard_nodes
    onset = run.first_onset_s
    fail = run.failure_s
    end = run.t_s[-1]
    res: dict = {"scenario": run.scenario, "seed": run.seed, "days": round(run.days, 3),
                 "hazard_nodes": int(hazard.sum())}

    # Node warning episodes and false ones (start where no hazard exists yet).
    prev = np.vstack([np.zeros((1, n), int), lv[:-1]])
    starts = (lv >= 2) & (prev < 2)
    clean = t < onset  # before any hazard process in this run (all of it for normal/faults)
    res["false_warnings"] = int((starts & clean[:, None]).sum())
    res["false_watches"] = int((((lv >= 1) & (prev < 1)) & clean[:, None]).sum())
    clean_days = max(0.0, (min(end, onset) - run.t_s[0]) / 86400)
    res["node_months_clean"] = n * clean_days / 30.0
    site_prev = np.concatenate([[0], out["site"][:-1]])
    res["false_sirens"] = int((((out["site"] >= 2) & (site_prev < 2)) & clean).sum())
    res["months_clean"] = clean_days / 30.0

    if hazard.any():
        horizon = fail if fail is not None else end
        for name, thr in (("warning", 2), ("watch", 1)):
            hit = (lv[:, hazard] >= thr).any(1) & (t >= onset) & (t <= horizon)
            first = t[hit][0] if hit.any() else None
            res[f"detected_{name}"] = first is not None
            res[f"lead_h_{name}"] = None if first is None or fail is None else round((fail - first) / 3600, 2)
            res[f"latency_h_{name}"] = None if first is None else round((first - onset) / 3600, 2)
        res["failure_h"] = None if fail is None else round((fail - run.t_s[0]) / 3600, 2)
        res["onset_h"] = round((onset - run.t_s[0]) / 3600, 2)
        # Spatial precision: share of nodes warned before failure that are hazard nodes.
        warned = (lv[(t >= onset) & (t <= horizon)] >= 2).any(0)
        res["warned_nodes"] = int(warned.sum())
        res["warned_hazard_nodes"] = int((warned & hazard).sum())
        # Time-to-failure accuracy in the last 12 h.
        if fail is not None:
            win = (t >= fail - 12 * 3600) & (t < fail)
            errs = []
            cover = []
            for e in np.nonzero(win)[0]:
                left = (fail - t[e]) / 3600
                est = out["ttf"][e, hazard]
                cover.append(np.isfinite(est).any())
                if np.isfinite(est).any():
                    errs.append(abs(np.nanmedian(est) - left))
            res["ttf_abs_err_h"] = round(float(np.median(errs)), 2) if errs else None
            res["ttf_coverage"] = round(float(np.mean(cover)), 3) if cover else None

    faulty = [i for i, f in enumerate(run.fault) if f]
    if faulty:
        flagged = out["suspect"].any(0)
        res["faults"] = {run.fault[i]: bool(flagged[i]) for i in faulty}
    res["ms_median"] = round(float(np.median(out["ms"])), 2) if len(out["ms"]) else None
    res["ms_p95"] = round(float(np.percentile(out["ms"], 95)), 2) if len(out["ms"]) else None
    return res


def aggregate(rows: list[dict]) -> dict:
    out: dict = {}
    for cfg in sorted({r["config"] for r in rows}):
        rs = [r for r in rows if r["config"] == cfg]
        agg: dict = {}
        fw = sum(r["false_warnings"] for r in rs)
        nm = sum(r["node_months_clean"] for r in rs)
        agg["false_warnings"] = fw
        agg["node_months"] = round(nm, 2)
        agg["false_alarms_per_node_month"] = round(fw / nm, 4) if nm else None
        # One-sided 95% upper bound for a Poisson rate (rule of three when zero events).
        agg["false_alarms_per_node_month_95ub"] = round(_poisson_ub(fw) / nm, 4) if nm else None
        agg["false_watches_per_node_month"] = round(sum(r["false_watches"] for r in rs) / nm, 4) if nm else None
        mo = sum(r["months_clean"] for r in rs)
        agg["false_sirens_per_month"] = round(sum(r["false_sirens"] for r in rs) / mo, 3) if mo else None
        for sc in HAZARD_SCENARIOS:
            hs = [r for r in rs if r["scenario"] == sc]
            if not hs:
                continue
            d = {"runs": len(hs), "recall_warning": round(np.mean([r["detected_warning"] for r in hs]), 3),
                 "recall_watch": round(np.mean([r["detected_watch"] for r in hs]), 3)}
            for name in ("warning", "watch"):
                leads = [r[f"lead_h_{name}"] for r in hs if r[f"lead_h_{name}"] is not None]
                if leads:
                    d[f"lead_h_{name}_median"] = round(float(np.median(leads)), 2)
                    d[f"lead_h_{name}_min"] = round(float(np.min(leads)), 2)
                    d[f"lead_h_{name}_p10"] = round(float(np.percentile(leads, 10)), 2)
            errs = [r["ttf_abs_err_h"] for r in hs if r.get("ttf_abs_err_h") is not None]
            if errs:
                d["ttf_abs_err_h_median"] = round(float(np.median(errs)), 2)
                d["ttf_coverage_median"] = round(float(np.median([r["ttf_coverage"] for r in hs
                                                                 if r.get("ttf_coverage") is not None])), 3)
            wn = sum(r["warned_nodes"] for r in hs)
            d["warned_nodes_on_hazard_share"] = round(sum(r["warned_hazard_nodes"] for r in hs) / wn, 3) if wn else None
            agg[sc] = d
        fl = [ok for r in rs for ok in (r.get("faults") or {}).values()]
        if fl:
            agg["fault_flag_rate"] = round(float(np.mean(fl)), 3)
            kinds: dict[str, list[bool]] = {}
            for r in rs:
                for k, v in (r.get("faults") or {}).items():
                    kinds.setdefault(k, []).append(v)
            agg["fault_flag_by_kind"] = {k: round(float(np.mean(v)), 3) for k, v in sorted(kinds.items())}
        ms = [r["ms_median"] for r in rs if r["ms_median"] is not None]
        agg["eval_ms_median"] = round(float(np.median(ms)), 2) if ms else None
        out[cfg] = agg
    return out


def _git_rev() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _poisson_ub(k: int) -> float:
    # Exact one-sided 95% upper limits for small counts, normal approximation beyond.
    table = [3.0, 4.74, 6.3, 7.75, 9.15, 10.51, 11.84, 13.15, 14.43, 15.71]
    return table[k] if k < len(table) else k + 1.645 * np.sqrt(k) + 1


# ------------------------------------------------------------------ driver
_G: dict = {}


def _task(args: tuple) -> list[dict]:
    scenario, seed = args
    site, source, configs, loss = _G["site"], _G["source"], _G["configs"], _G["loss"]
    run = make_run(source, site, scenario, seed, HOURS[scenario] if source != "surrogate" else None)
    run = drop_packets(run, loss, seed)
    comm = commission_window(source, site, seed) if _G["commission"] else None
    rows = []
    for name in configs:
        out = replay(run, site, CONFIGS[name], _G["model"], _G["rules"], comm)
        res = score_run(run, out, site.mesh.spacing_m)
        res["config"] = name
        rows.append(res)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["sim", "surrogate"], default="sim")
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--first-seed", type=int, default=1)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--loss", type=float, default=0.02, help="random packet loss applied to the runs")
    ap.add_argument("--no-commission", action="store_true", help="skip the install-day thermal calibration")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--out", default="reports/kpi")
    args = ap.parse_args(argv)

    from achal_common import load_site

    site = load_site()
    t0 = time.time()
    print(f"training on normal seeds {TRAIN_SEEDS} ({args.source})", flush=True)
    model, rules = train(args.source, site)
    _G.update(site=site, source=args.source, configs=args.configs.split(","), model=model, rules=rules,
              loss=args.loss, commission=not args.no_commission)
    tasks = [(sc, s) for sc in args.scenarios.split(",") for s in range(args.first_seed, args.first_seed + args.seeds)]
    print(f"{len(tasks)} runs x {len(_G['configs'])} configs on {args.workers} workers", flush=True)
    rows: list[dict] = []
    ctx = mp.get_context("fork")
    with ctx.Pool(args.workers) as pool:
        for i, part in enumerate(pool.imap_unordered(_task, tasks), 1):
            rows.extend(part)
            r = next(p for p in part if p["config"] == "full")
            print(f"[{i}/{len(tasks)}] {r['scenario']} seed {r['seed']}: "
                  f"lead_h={r.get('lead_h_warning')} fw={r['false_warnings']}", flush=True)

    summary = aggregate(rows)
    meta = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "synthetic": True,
        "source": "W1 field simulator (achal_sim)" if args.source == "sim" else "built-in surrogate generator",
        "site": site.site_id, "scenarios": args.scenarios.split(","), "seeds": args.seeds,
        "first_seed": args.first_seed, "train_seeds": list(TRAIN_SEEDS), "hours": HOURS,
        "packet_loss": args.loss, "commissioned": not args.no_commission,
        "engine": asdict(CONFIGS["full"]), "rule_thresholds": {k: v[:3] for k, v in rules.thresholds.items()},
        "iforest_train_rows": model.train_rows, "python": platform.python_version(), "git": _git_rev(),
        "wall_s": round(time.time() - t0, 1),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "kpi-report.json").write_text(json.dumps({"meta": meta, "summary": summary}, indent=2), encoding="utf-8")
    keys = sorted({k for r in rows for k in r if k != "faults"})
    with (out_dir / "kpi-runs.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys + ["faults"])
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["config"], r["scenario"], r["seed"])):
            faults = json.dumps(r["faults"]) if r.get("faults") else ""
            w.writerow({**{k: r.get(k) for k in keys}, "faults": faults})
    print(json.dumps(summary["full"] if "full" in summary else summary, indent=2))
    print(f"wrote {out_dir}/kpi-report.json and kpi-runs.csv in {meta['wall_s']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

