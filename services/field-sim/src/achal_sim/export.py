# SPDX-License-Identifier: MIT
"""Write labelled scenario runs to JSON Lines for the risk engine, KPI harness and jury.

    python -m achal_sim.export --scenario all --seed 42                 # full-resolution runs -> data/runs/
    python -m achal_sim.export --samples                                # small committed set -> data/samples/*.jsonl.gz
    python -m achal_sim.export --scenario pillar_collapse --plot docs/img

Every line is one telemetry v1 message with its `truth` labels. All data is synthetic.
A `<scenario>.manifest.json` next to each file records the seed, the scenario parameters
drawn from it, and the event onset/failure times, so any run can be regenerated exactly.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

from achal_common import load_site

from achal_sim.scenarios import SCENARIOS
from achal_sim.simulator import DEFAULT_START, FieldSimulator

REPO_ROOT = Path(__file__).resolve().parents[4]

# The committed sample set (gzipped): small enough for git (< 5 MB total), long enough to show each event.
# (scenario, sample period s, keep one in N, hours)
SAMPLE_SET = (
    ("normal", 300, 1, 48.0),
    ("trough", 1800, 1, 504.0),
    ("pillar_collapse", 60, 5, 48.0),
    ("faults", 300, 1, 72.0),
    ("fire", 300, 1, 96.0),
)


def run(scenario: str, seed: int, hours: float | None, every: int, period_s: float | None,
        start: datetime) -> tuple[FieldSimulator, Iterator[dict]]:
    sim = FieldSimulator(load_site(), scenario, seed, start, period_s, hours)
    steps = int(round(sim.plan.duration_h * 3600 / sim.period_s))

    def rows() -> Iterator[dict]:
        for i in range(steps):
            batch = sim.step()
            if i % every == 0:
                yield from batch
            elif any(m["truth"]["event"] == "blast" or (m["truth"]["event"] == "collapse" and m["vib_peak_g"] > 0.05)
                     for m in batch):
                yield from batch  # never decimate away a blast or the collapse burst

    return sim, rows()


def write(path: Path, rows: Iterable[dict]) -> Counter:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    counts: Counter = Counter()
    with opener(path, "wt", encoding="utf-8") as f:
        for row in rows:
            counts[row["truth"]["event"]] += 1
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return counts


def manifest(sim: FieldSimulator, counts: Counter, every: int, path: Path) -> dict:
    plan = sim.plan

    def at(h: float | None) -> str | None:
        return None if h is None else (sim.start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "synthetic": True,
        "file": path.name,
        "site": sim.site.site_id,
        "scenario": plan.name,
        "seed": plan.seed,
        "start": at(0.0),
        "hours": plan.duration_h,
        "period_s": sim.period_s,
        "every": every,
        "onset_ts": at(plan.onset_h),
        "failure_ts": at(plan.failure_h),
        "hazard_nodes": sorted(plan.trough_nodes | plan.pothole_nodes) or None,
        "faults": {f.node: {"kind": f.kind, "onset_ts": at(f.onset_h)} for f in plan.faults.values()} or None,
        "params": plan.params,
        "rows_by_event": dict(counts),
        "regenerate": f"python -m achal_sim.export --scenario {plan.name} --seed {plan.seed} --hours {plan.duration_h}"
                      f" --period-s {sim.period_s:g} --every {every}",
    }


def load_jsonl(path: str | Path) -> Iterator[dict]:
    """Read a run written by this module (.jsonl or .jsonl.gz)."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def export_one(scenario: str, seed: int, hours: float | None, every: int, period_s: float | None, out: Path,
               start: datetime, gz: bool) -> Path:
    sim, rows = run(scenario, seed, hours, every, period_s, start)
    path = out / f"{scenario}.jsonl{'.gz' if gz else ''}"
    counts = write(path, rows)
    meta = manifest(sim, counts, every, path)
    (out / f"{scenario}.manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"{path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}: "
          f"{sum(counts.values())} rows {dict(counts)} {path.stat().st_size / 1e6:.2f} MB", file=sys.stderr)
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="all", help=f"all or comma list of: {', '.join(SCENARIOS)}")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--hours", type=float, help="run length (default: per scenario)")
    ap.add_argument("--every", type=int, default=1, help="keep one sample period in N")
    ap.add_argument("--period-s", type=float, help="sample period (default: site config)")
    ap.add_argument("--start", default=DEFAULT_START.isoformat(), help="ISO time of t=0")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "runs")
    ap.add_argument("--gzip", action="store_true")
    ap.add_argument("--samples", action="store_true", help="write the small committed set to data/samples/")
    ap.add_argument("--plot", type=Path, help="also draw one PNG per scenario into this folder (needs matplotlib)")
    args = ap.parse_args(argv)
    start = datetime.fromisoformat(args.start)

    if args.samples:
        out = REPO_ROOT / "data" / "samples"
        paths = [export_one(sc, args.seed, h, every, period, out, start, True) for sc, period, every, h in SAMPLE_SET]
    else:
        names = SCENARIOS if args.scenario == "all" else tuple(args.scenario.split(","))
        paths = [export_one(sc, args.seed, args.hours, args.every, args.period_s, args.out, start, args.gzip)
                 for sc in names]
    if args.plot:
        from achal_sim.plot import plot_run

        for path in paths:
            png = plot_run(path, args.plot)
            print(f"plot: {png}", file=sys.stderr)


if __name__ == "__main__":
    main()
