# SPDX-License-Identifier: MIT
"""Draw one exported run as a PNG for docs and the pitch (optional; needs matplotlib).

Five stacked panels share the time axis: true subsidence, measured tilt, crack gauge,
vibration peak and node temperature. Up to three most-affected nodes are coloured and one
unaffected node is drawn in grey for contrast; onset and failure are marked.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from achal_sim.export import load_jsonl  # noqa: E402

SERIES = ("#2a78d6", "#eb6834", "#1baf7a")  # categorical slots 1-3
REFERENCE = "#8d8c87"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SURFACE = "#fcfcfb"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def plot_run(path: str | Path, out_dir: str | Path) -> Path:
    path = Path(path)
    name = path.name.split(".")[0]
    manifest_path = path.with_name(f"{name}.manifest.json")
    meta = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    by_node: dict[str, list[dict]] = defaultdict(list)
    for row in load_jsonl(path):
        by_node[row["node"]].append(row)

    def severity(rows: list[dict]) -> float:
        return max(abs(r["truth"]["subsidence_mm"]) + 100 * (r["truth"].get("event") == "fire")
                   + 1000 * bool(r["truth"].get("fault")) for r in rows)

    ranked = sorted(by_node, key=lambda n: severity(by_node[n]), reverse=True)
    picks = [n for n in ranked[:3] if severity(by_node[n]) > 0.5] or ranked[:1]
    reference = ranked[-1]

    fig, axes = plt.subplots(5, 1, figsize=(10, 11), sharex=True, facecolor=SURFACE)
    panels = (
        ("True subsidence (mm)", lambda r: r["truth"]["subsidence_mm"]),
        ("Tilt magnitude (deg)", lambda r: math.hypot(r["tilt_x_deg"], r["tilt_y_deg"])),
        ("Crack gauge (mm)", lambda r: r["crack_mm"]),
        ("Vibration peak (g)", lambda r: r["vib_peak_g"]),
        ("Node temperature (°C)", lambda r: r["temp_c"]),
    )
    styles = [(reference, REFERENCE, 1.2)] + [(n, c, 2.0) for n, c in zip(picks, SERIES, strict=False)]
    for ax, (label, fn) in zip(axes, panels, strict=True):
        ax.set_facecolor(SURFACE)
        for node, colour, width in styles:
            rows = by_node[node]
            ax.plot([_ts(r["ts"]) for r in rows], [fn(r) for r in rows], color=colour, lw=width,
                    label=f"{node}" + (" (reference)" if node == reference else ""))
        ax.set_ylabel(label, color=MUTED, fontsize=9)
        ax.grid(True, color=GRID, lw=0.6)
        ax.tick_params(colors=MUTED, labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        for key, text in (("onset_ts", "onset"), ("failure_ts", "failure")):
            if meta.get(key):
                ax.axvline(_ts(meta[key]), color=MUTED, lw=1, ls="--")
                if ax is axes[0]:
                    ax.annotate(text, (_ts(meta[key]), 1.0), xycoords=("data", "axes fraction"), xytext=(3, -10),
                                textcoords="offset points", color=MUTED, fontsize=8)
    # Symlog keeps millimetre precursors visible next to metre-scale collapses.
    axes[0].set_yscale("symlog", linthresh=10)
    axes[1].set_yscale("symlog", linthresh=0.05)
    for ax in axes[:2]:
        ax.set_ylim(bottom=0)
    axes[3].set_yscale("log")
    axes[4].xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M"))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", fontsize=8, frameon=False, ncols=4, labelcolor=INK)
    title = f"ACHAL synthetic run: {name} (seed {meta.get('seed', '?')}, site {meta.get('site', '?')})"
    fig.suptitle(title, color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    png = out / f"scenario-{name}.png"
    fig.savefig(png, dpi=110, facecolor=SURFACE)
    plt.close(fig)
    return png
