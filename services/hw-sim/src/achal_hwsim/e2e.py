# SPDX-License-Identifier: MIT
"""The guide's end-to-end deliverable: a movement event travelling from a sensor
node through the network to a dashboard alert, with sensor plots, node health,
battery estimate and network metrics.

    python -m achal_hwsim.e2e --scenario critical --arch wifi_mesh --out runs/critical

Defaults are the guide's "suggested first experiment": 20 virtual nodes, 10 m
spacing, 1 s reporting (vibration sampled separately at 200 Hz), normal /
warning / critical scenarios, and four failure cases injected into every run:
lost packets (a 60 s, 25 dB fade), an offline node, a gateway outage and a
battery fault that ends in a low-battery warning and a brown-out.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from . import network as N
from .gateway import Gateway
from .node import NodeCfg, VirtualNode
from .scenarios import DISP_WARN, TILT_WARN, Fault, build

# A node counts as "clearly normal" if its true movement stays below GUARD x the warning thresholds.
# Between GUARD and 1.0 is a grey zone where sensor noise makes an early alarm legitimate; both
# 0.5 (primary) and 0.8 (strict) are reported.
GUARDS = (0.5, 0.8)


def default_failures(sc, seed: int) -> tuple[N.Failures, dict]:
    rng = np.random.default_rng([seed, 5])
    D, n = sc.duration_s, sc.n
    pick = [int(k) for k in rng.choice(n, 2, replace=False)]
    f = N.Failures(
        node_down={pick[0]: [(0.45 * D, 0.60 * D)]},
        gw_down=[(0.62 * D, 0.62 * D + 90.0)],
        extra_loss_db=[(0.30 * D, 0.30 * D + 60.0, 25.0)],
    )
    sc.faults.append(Fault(pick[0], "dead", 0.45 * D, 0.60 * D))
    sc.faults.append(Fault(pick[1], "batt_leak", 0.0, D, rate=600.0))
    info = {
        "lost_packets": {"t0": 0.30 * D, "t1": 0.30 * D + 60, "extra_loss_db": 25.0},
        "offline_node": {"node": f"n{pick[0] + 1:02d}", "t0": 0.45 * D, "t1": 0.60 * D},
        "gateway_outage": {"t0": 0.62 * D, "t1": 0.62 * D + 90.0},
        "low_battery": {"node": f"n{pick[1] + 1:02d}", "soc0": 0.09, "leak_ma": 600.0},
    }
    return f, info


def run(
    scenario: str = "critical",
    arch: str = "wifi_mesh",
    n: int = 20,
    spacing_m: float = 10.0,
    interval_s: float = 1.0,
    duration_s: float = 1800.0,
    seed: int = 1,
    failures: bool = True,
    node_cfg: NodeCfg | None = None,
    out: Path | None = None,
    backhaul_km: float = 3.0,
    resync_s: float = 60.0,
) -> dict:
    t_start = time.time()
    sc = build(scenario, seed, n, spacing_m, duration_s, interval_s)
    fails, finfo = default_failures(sc, seed) if failures else (N.Failures(), {})
    gw_xy = (float(sc.xy[:, 0].min() - 10.0), float(sc.xy[:, 1].mean()))
    radio = N.WIFI if arch in ("wifi_mesh", "hybrid") else N.lora_radio(7)
    topo = N.Topology(sc.xy, gw_xy, radio, seed)
    roles = ["leaf"] * n
    if arch in ("wifi_mesh", "hybrid"):
        parents = N.WifiMesh(topo, np.random.default_rng(0)).tree(frozenset())
        for _c, p in parents.items():
            if p != topo.GW:
                roles[p] = "router"
    base = node_cfg or NodeCfg()
    nodes = []
    for i in range(n):
        cfg = NodeCfg(
            **{
                **asdict(base),
                "interval_s": interval_s,
                "arch": "wifi_mesh" if arch == "hybrid" else arch,
                "role": roles[i],
            }
        )
        soc0 = (
            finfo.get("low_battery", {}).get("soc0", 0.8)
            if failures and f"n{i + 1:02d}" == finfo.get("low_battery", {}).get("node")
            else 0.8
        )
        nodes.append(VirtualNode(i, sc, cfg, seed, soc0=soc0))

    # 1. nodes sample, process and packetise
    packets: dict[tuple[int, int], dict] = {}
    sends = []
    # Each node keeps its own schedule (boot time, clock): a fixed phase within the interval.
    # Synchronised reporting would collapse ALOHA (option B) - see the report.
    phase = np.random.default_rng([seed, 3]).uniform(0, interval_s, n)
    for t in sc.times() + interval_s:
        for nd in nodes:
            if resync_s and (t % resync_s) < interval_s:
                nd.resync(t)
            p = nd.step(t)
            if p is None:
                continue
            packets[(nd.idx, p["seq"])] = p
            sends.append((nd.idx, p["seq"], t + phase[nd.idx] + 0.006 + float(nd.rng.uniform(0, 0.002))))

    # 2. network
    dl = N.simulate(arch, sc.xy, interval_s, duration_s, seed, fails, gw_xy, sends=sends)

    # 3. gateway, in arrival order, with a 1 s housekeeping tick
    gw = Gateway([nd.name for nd in nodes], sc.xy, interval_s)
    ok = sorted((d for d in dl if d.t_rx is not None), key=lambda d: d.t_rx)
    ticks = np.arange(0, duration_s + 120, 1.0)
    rows_rx, snaps = [], []
    k = 0
    bh = N.Backhaul(distance_km=backhaul_km) if arch == "hybrid" else None
    bh_rng = np.random.default_rng([seed, 11])
    for tk in ticks:
        while k < len(ok) and ok[k].t_rx <= tk:
            d = ok[k]
            p = packets[(d.idx, d.seq)]
            evs = gw.ingest(p, d.t_rx, d.rssi_dbm, d.hops)
            if bh is not None:
                for e in evs:
                    lat, _ = bh.event_latency_s(bh_rng)
                    e.detail += f" | office +{lat:.2f} s" if lat is not None else " | office: backhaul lost"
                    e.t_office = None if lat is None else e.t + lat
            rows_rx.append(
                {
                    "node": p["node"],
                    "seq": d.seq,
                    "t_true": p["t_true"],
                    "t_tx": d.t_tx,
                    "t_rx": d.t_rx,
                    "delay_ms": (d.t_rx - d.t_tx) * 1000,
                    "hops": d.hops,
                    "rssi_dbm": d.rssi_dbm,
                    "buffered": d.buffered,
                    "ts_err_ms": (p["ts_node"] - p["t_true"]) * 1000,
                }
            )
            k += 1
        gw.tick(tk)
        if tk % 10 == 0:
            snaps += gw.snapshot(tk)

    # 4. evaluation against ground truth
    pk = pd.DataFrame([{**{k2: v for k2, v in p.items() if k2 != "truth"}, **p["truth"]} for p in packets.values()])
    ev = pd.DataFrame([e.as_dict() for e in gw.events])
    res = evaluate(sc, pk, ev, interval_s)
    net = N.metrics(dl, n, interval_s, duration_s)
    if arch == "lorawan":
        net["aloha_G_per_channel"] = N.LoRaWanStar(topo, np.random.default_rng(0)).offered_load(interval_s)
    if arch in ("wifi_mesh", "hybrid"):
        wm = N.WifiMesh(topo, np.random.default_rng(0))
        net["channel_busy_fraction"] = wm.channel_busy_fraction(n / interval_s, net["mean_hops"])
    if bh is not None:
        net["backhaul"] = {
            "distance_km": backhaul_km,
            "rx_dbm": bh.rx_dbm(),
            "sf": bh.pick_sf(),
            "utilisation_if_raw": bh.utilisation_raw(n, interval_s),
            "utilisation_summaries": bh.utilisation_summary(n),
        }
    rx = pd.DataFrame(rows_rx)
    out_m = {
        "config": {
            "scenario": scenario,
            "arch": arch,
            "n": n,
            "spacing_m": spacing_m,
            "interval_s": interval_s,
            "duration_s": duration_s,
            "seed": seed,
            "failures": finfo,
            "node": asdict(base),
            "epicentre": sc.epicentre,
        },
        "network": net,
        "detection": res,
        "timestamp_error_ms": {
            "p50": float(rx.ts_err_ms.abs().median()) if len(rx) else None,
            "p95": float(rx.ts_err_ms.abs().quantile(0.95)) if len(rx) else None,
        },
        "energy": {
            "load_ma_by_role": {
                r: float(np.mean([nd.load_ma for nd in nodes if nd.cfg.role == r])) for r in set(roles)
            },
            "soc_end_min": float(min(nd.cell.soc for nd in nodes)),
        },
        "runtime_s": time.time() - t_start,
    }
    if out:
        out.mkdir(parents=True, exist_ok=True)
        pk.to_csv(out / "packets.csv.gz", index=False)
        rx.to_csv(out / "deliveries.csv.gz", index=False)
        pd.DataFrame(snaps).to_csv(out / "snapshots.csv.gz", index=False)
        ev.to_json(out / "events.json", orient="records", indent=1)
        pd.DataFrame(
            {"node": [f"n{i + 1:02d}" for i in range(n)], "x": sc.xy[:, 0], "y": sc.xy[:, 1], "role": roles}
        ).to_csv(out / "nodes.csv", index=False)
        (out / "metrics.json").write_text(json.dumps(out_m, indent=1, default=float))
    return out_m


def evaluate(sc, pk: pd.DataFrame, ev: pd.DataFrame, interval_s: float) -> dict:
    """Node-level detection, latency and false alarms against the scenario's ground truth."""
    res: dict = {"per_level": {}, "false_alarms": {}, "vibration": {}}
    ground = ev[ev.kind.isin(["ground_warning", "ground_critical"])] if len(ev) else ev
    for lvl, kinds in ((1, ["ground_warning", "ground_critical"]), (2, ["ground_critical"])):
        lat, hit, total = [], 0, 0
        for i in range(sc.n):
            name = f"n{i + 1:02d}"
            g = pk[(pk.node == name) & (pk.label >= lvl)]
            if g.empty:
                continue
            total += 1
            t_true = g.t_true.min()
            a = ground[(ground.node == name) & ground.kind.isin(kinds)] if len(ground) else ground
            if len(a):
                hit += 1
                lat.append(float(a.t.min() - t_true))
        res["per_level"][str(lvl)] = {
            "nodes_truly_at_level": total,
            "nodes_alerted": hit,
            "recall": hit / total if total else None,
            "latency_s_median": float(np.median(lat)) if lat else None,
            "latency_s_p95": float(np.percentile(lat, 95)) if lat else None,
            "latency_s_min": float(np.min(lat)) if lat else None,
        }
    hours = sc.duration_s / 3600
    for guard in GUARDS:
        clear_n = fa_n = 0
        for i in range(sc.n):
            name = f"n{i + 1:02d}"
            g = pk[pk.node == name]
            if g.empty:
                continue
            if (g.tilt_deg.max() < guard * TILT_WARN) and (g.crack_mm.max() < guard * DISP_WARN):
                clear_n += 1
                if len(ground) and (ground.node == name).any():
                    fa_n += 1
        res["false_alarms"][f"guard_{guard}"] = {
            "clear_nodes": clear_n,
            "false_alarm_nodes": fa_n,
            "per_node_hour": fa_n / max(clear_n * hours, 1e-9),
        }
    truth_vib = pk.groupby("node").vib_exceed.any()
    seen_vib = pk.groupby("node").flags.apply(lambda c: bool((c.astype(int) & 64).any()))
    res["vibration"] = {
        "nodes_truly_exceeding_dgms": int(truth_vib.sum()),
        "nodes_flagging_vibration": int(seen_vib.sum()),
        "nodes_flagging_without_exceedance": int((seen_vib & ~truth_vib).sum()),
    }
    # Earliest alert latency for the whole field (what a mine office sees first)
    if len(ground) and (pk.label >= 1).any():
        res["first_alert_after_first_truth_s"] = float(ground.t.min() - pk[pk.label >= 1].t_true.min())
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="critical", choices=["normal", "warning", "critical"])
    ap.add_argument("--arch", default="wifi_mesh", choices=["wifi_mesh", "lorawan", "hybrid"])
    ap.add_argument("--nodes", type=int, default=20)
    ap.add_argument("--spacing", type=float, default=10.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=1800.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-failures", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    m = run(a.scenario, a.arch, a.nodes, a.spacing, a.interval, a.duration, a.seed, not a.no_failures, out=a.out)
    print(
        json.dumps(
            {k: m[k] for k in ("network", "detection", "timestamp_error_ms", "energy", "runtime_s")},
            indent=1,
            default=float,
        )
    )


if __name__ == "__main__":
    main()
