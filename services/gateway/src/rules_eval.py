# SPDX-License-Identifier: MIT
"""Score the gateway's local siren rules on the W1 scenarios.

    PYTHONPATH=services/field-sim/src:services/gateway/src python services/gateway/src/rules_eval.py --seeds 10

For each scenario and seed, streams the simulator through `LocalRules` and
reports when the first siren sounded relative to ground truth: lead time
before the pillar collapse / trough damage, and any siren in scenarios with
no hazard (normal, faults) as a false siren. Writes docs/gateway-rules-eval.md
and data/gateway-rules-eval.json. All data is synthetic.
"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from achal_common import Telemetry, load_site
from achal_sim import FieldSimulator
from rules import LocalRules, Thresholds

REPO = Path(__file__).resolve().parents[3]
SCENARIOS = ("normal", "trough", "pillar_collapse", "faults", "fire")
HAZARD = {"trough", "pillar_collapse", "fire"}


def run_one(scenario: str, seed: int, period_s: float) -> dict:
    site = load_site("jharia-demo")
    start = datetime(2026, 10, 1, tzinfo=UTC)
    sim = FieldSimulator(site, scenario, seed, start, period_s)
    rules = LocalRules(site, Thresholds())
    steps = int(round(sim.plan.duration_h * 3600 / sim.period_s))
    first_on = first_level = None
    critical_at = None
    for _ in range(steps):
        for msg in sim.step():
            for d in rules.observe(Telemetry.model_validate(msg)):
                if d.on and first_on is None:
                    first_on, first_level = d.ts, d.level
                if d.on and d.level == "critical" and critical_at is None:
                    critical_at = d.ts
    hours = lambda ts: None if ts is None else round((ts - start).total_seconds() / 3600, 2)  # noqa: E731
    failure_h = sim.plan.failure_h
    on_h = hours(first_on)
    return {
        "scenario": scenario, "seed": seed, "period_s": period_s, "duration_h": sim.plan.duration_h,
        "onset_h": sim.plan.onset_h, "failure_h": failure_h, "first_siren_h": on_h, "first_level": first_level,
        "first_critical_h": hours(critical_at),
        "lead_h": round(failure_h - on_h, 2) if failure_h is not None and on_h is not None else None,
    }


def summarise(runs: list[dict]) -> list[dict]:
    rows = []
    for sc in SCENARIOS:
        rs = [r for r in runs if r["scenario"] == sc]
        if not rs:
            continue
        sounded = [r for r in rs if r["first_siren_h"] is not None]
        if sc in HAZARD:
            before = [r for r in sounded if r["lead_h"] is not None and r["lead_h"] > 0]
            leads = [r["lead_h"] for r in before]
            rows.append({"scenario": sc, "runs": len(rs), "sirens": len(sounded), "before_failure": len(before),
                         "lead_h_median": round(statistics.median(leads), 2) if leads else None,
                         "lead_h_min": min(leads) if leads else None, "false_sirens": None})
        else:
            rows.append({"scenario": sc, "runs": len(rs), "sirens": len(sounded), "before_failure": None,
                         "lead_h_median": None, "lead_h_min": None, "false_sirens": len(sounded)})
    return rows


def markdown(rows: list[dict], args) -> str:
    out = ["# Gateway local siren rules on the simulator scenarios (synthetic)", "",
           f"`python services/gateway/src/rules_eval.py --seeds {args.seeds} --period-s {args.period_s:g}` "
           f"on {datetime.now(UTC).date().isoformat()}, site jharia-demo, default thresholds "
           "(see [gateway.md](gateway.md)). Lead time is failure time minus first siren. "
           "This is the no-cloud fallback layer; the AI risk engine is scored separately in kpis.md.", "",
           "| Scenario | Runs | Runs with a siren | Siren before failure | Median lead (h) | Shortest lead (h) "
           "| False sirens |", "| --- | --- | --- | --- | --- | --- | --- |"]
    dash = "—"
    for r in rows:
        out.append(f"| {r['scenario']} | {r['runs']} | {r['sirens']} | "
                   f"{dash if r['before_failure'] is None else r['before_failure']} | "
                   f"{dash if r['lead_h_median'] is None else r['lead_h_median']} | "
                   f"{dash if r['lead_h_min'] is None else r['lead_h_min']} | "
                   f"{dash if r['false_sirens'] is None else r['false_sirens']} |")
    out += ["", "Per-run numbers: `data/gateway-rules-eval.json`.", ""]
    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seeds", type=int, default=10)
    p.add_argument("--period-s", type=float, default=300)
    p.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    p.add_argument("--out", type=Path, default=REPO / "data" / "gateway-rules-eval.json")
    p.add_argument("--md", type=Path, default=REPO / "docs" / "gateway-rules-eval.md")
    args = p.parse_args()
    jobs = [(sc, seed, args.period_s) for sc in args.scenarios for seed in range(1, args.seeds + 1)]
    with ProcessPoolExecutor() as pool:
        runs = list(pool.map(run_one, *zip(*jobs, strict=True)))
    rows = summarise(runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"synthetic": True, "thresholds": Thresholds().__dict__, "summary": rows,
                                    "runs": runs}, indent=1))
    args.md.write_text(markdown(rows, args), encoding="utf-8")
    print(markdown(rows, args))
    for r in runs:
        print(r)


if __name__ == "__main__":
    main()
