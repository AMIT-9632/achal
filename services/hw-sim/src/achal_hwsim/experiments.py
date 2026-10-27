# SPDX-License-Identifier: MIT
"""All experiments behind docs/hardware-sim/report.md, reproducible from one command.

    python -m achal_hwsim.experiments            # everything (~15 min on 4 cores)
    python -m achal_hwsim.experiments --only e4  # one experiment
    python -m achal_hwsim.experiments --quick    # fewer seeds, for CI

Writes numbers to reports/hwsim/<exp>.json and figures to docs/hardware-sim/img/.
Every stochastic result is repeated over seeds and reported as mean with a 95 %
interval (normal approximation) or as min/max where that is more honest.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import dsp  # noqa: E402
from . import network as N  # noqa: E402
from .components import esp32, power  # noqa: E402
from .components.mpu6050 import Mpu6050, tilt_noise_floor_deg  # noqa: E402
from .components.vl53l0x import AMBIENT_MCPS, TofConditions, Vl53l0x  # noqa: E402
from .scenarios import grid  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "reports" / "hwsim"
IMG = REPO / "docs" / "hardware-sim" / "img"
# Reference categorical palette (dataviz skill, light mode), fixed order.
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update(
    {
        "figure.dpi": 130,
        "savefig.dpi": 130,
        "font.size": 9,
        "axes.edgecolor": INK2,
        "axes.labelcolor": INK,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.linewidth": 2.0,
        "legend.frameon": False,
        "figure.facecolor": "#fcfcfb",
        "axes.facecolor": "#fcfcfb",
    }
)


def ci95(x) -> list[float]:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return [float("nan")] * 3
    m = float(x.mean())
    h = float(1.96 * x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else 0.0
    return [m, m - h, m + h]


def save(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, default=float))


def fig(name: str) -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(IMG / f"{name}.png")
    plt.close()


# --- E1: MPU6050 -----------------------------------------------------------------


def e1_mpu(seeds: int) -> dict:
    """Tilt resolution vs averaging; thermal drift with and without compensation over a Jharia day; PPV floor."""
    res: dict = {"noise_floor_deg": {n: tilt_noise_floor_deg(n) for n in (1, 50, 200, 400, 2000)}}
    # Monte Carlo of the same: n-sample means at constant temperature
    mc = []
    for s in range(seeds):
        m = Mpu6050(np.random.default_rng(s))
        tx = [dsp.tilt_deg(*(m.sample_window(0, 0, 25, 200, 200)[0].mean(0) / 16384))[0] for _ in range(200)]
        mc.append(np.std(tx))
    res["mc_tilt_sigma_200_samples_deg"] = ci95(mc)
    # Thermal: box temperature over a hot-season day (NASA POWER April mean 30.7 C, +-7 C air, +25 C sun on the box)
    hours = np.arange(0, 24, 0.25)
    t_air = 30.7 + 7 * np.sin(2 * np.pi * (hours - 9) / 24)
    sun = np.clip(np.sin(np.pi * (hours - 6) / 12), 0, None)
    t_box = t_air + 25 * sun
    t_buried = 30.7 + 1.5 * np.sin(2 * np.pi * (hours - 15) / 24)  # 0.5 m down a sleeve: damped and lagged
    curves = {"none": [], "comp": [], "comp_buried": []}
    for s in range(seeds):
        for mode in curves:
            m = Mpu6050(np.random.default_rng(100 + s))
            temps = t_buried if mode == "comp_buried" else t_box
            m.t_ref_c = temps[0]
            k = m.calibrate_tc() if mode.startswith("comp") else np.zeros(3)
            base, out = None, []
            for tb in temps:
                counts, tc = m.sample_window(0, 0, tb, 200, 200, gap_s=900)
                g = counts.mean(0) / 16384
                die = dsp.mpu_temp_c(tc)
                comp = [dsp.temp_comp(float(g[a]), float(k[a]), die, m.t_ref_c) for a in range(3)]
                tx, ty = dsp.tilt_deg(*comp)
                base = base or (tx, ty)
                out.append(np.hypot(tx - base[0], ty - base[1]))
            curves[mode].append(out)
    for mode in curves:
        a = np.array(curves[mode])
        res[f"diurnal_max_tilt_error_deg_{mode}"] = {
            "median": float(np.median(a.max(1))),
            "p90": float(np.percentile(a.max(1), 90)),
            "share_units_over_warning_0.1deg": float((a.max(1) >= 0.1).mean()),
        }
    plt.figure(figsize=(6.4, 3.2))
    for mode, col, lab in (
        ("none", C[1], "in the box, no compensation"),
        ("comp", C[0], "in the box, compensated"),
        ("comp_buried", C[2], "buried 0.5 m in the stake sleeve, compensated"),
    ):
        a = np.array(curves[mode])
        plt.fill_between(hours, np.percentile(a, 10, 0), np.percentile(a, 90, 0), color=col, alpha=0.18, lw=0)
        plt.plot(hours, np.median(a, 0), color=col, label=f"{lab} (median, 10-90 %)")
    plt.axhline(0.1, color=INK2, lw=1, ls="--")
    plt.text(14, 0.11, "warning threshold 0.10°", color=INK2, fontsize=8)
    plt.yscale("log")
    plt.xlabel("hour of day (April, Jharia; box +25 °C in full sun)")
    plt.ylabel("apparent tilt, no ground movement (°)")
    plt.legend(loc="upper left")
    fig("e1_mpu_thermal")
    # PPV noise floor: windows with no vibration
    m = Mpu6050(np.random.default_rng(7))
    ppv = [dsp.vib_features(m.sample_window(0, 0, 25, 200, 200)[0][:, 2] / 16384, 200).ppv_mm_s for _ in range(500)]
    res["ppv_noise_floor_mm_s"] = {"median": float(np.median(ppv)), "p99": float(np.percentile(ppv, 99))}
    # PPV estimator accuracy on clean sines
    acc = {}
    for f in (4, 6, 10, 15, 25, 40):
        t = np.arange(200) / 200
        a = 0.02 * np.sin(2 * np.pi * f * t + 0.4)
        v = dsp.vib_features(a, 200)
        acc[f] = {"ppv_true": 0.02 * 9806.65 / (2 * np.pi * f), "ppv_est": v.ppv_mm_s, "dom_hz": v.dom_hz}
    res["ppv_estimator"] = acc
    save("e1_mpu", res)
    return res


# --- E2: VL53L0X -------------------------------------------------------------------


def e2_tof(seeds: int) -> dict:
    res: dict = {"max_range_mm": {}, "valid_rate": {}, "sigma_mm": {}}
    for mode in ("default", "high_accuracy", "long_range"):
        tof = Vl53l0x(np.random.default_rng(0), mode)
        res["max_range_mm"][mode] = {
            f"{amb}/{name}": tof.max_range_mm(TofConditions(reflectance=r, ambient=amb))
            for amb in AMBIENT_MCPS
            for name, r in (("white", 0.88), ("grey", 0.17))
        }
    dists = np.array([100, 200, 300, 500, 700, 1000, 1200, 1500, 2000, 3000, 10000])
    plt.figure(figsize=(6.4, 3.2))
    for k, amb in enumerate(("shaded_tube", "overcast", "direct_sun")):
        tof = Vl53l0x(np.random.default_rng(1), "high_accuracy")
        rates, sig = [], []
        for d in dists:
            shots = tof.burst(float(d), TofConditions(ambient=amb, plate_half_mm=2000), 200)
            ok = [s for s in shots if s[1] == 0]
            rates.append(len(ok) / len(shots))
            sig.append(float(np.std([s[0] for s in ok])) if len(ok) > 5 else float("nan"))
        res["valid_rate"][amb] = dict(zip(dists.tolist(), rates, strict=False))
        res["sigma_mm"][amb] = dict(zip(dists.tolist(), sig, strict=False))
        plt.plot(dists / 1000, rates, marker="o", ms=4, color=C[k], label=amb.replace("_", " "))
    plt.axvline(10, color=INK2, lw=1, ls="--")
    plt.text(6.2, 0.5, "guide's 10 m\nnode spacing", color=INK2, fontsize=8)
    plt.xscale("log")
    plt.xlabel("distance to a large white target (m, log)")
    plt.ylabel("valid readings (share)")
    plt.legend(loc="lower left")
    fig("e2_tof_range")
    # Installed gauge: filtered reading error vs plate size at 0.5 m
    plate = {}
    for half in (40, 75, 125, 200):
        errs = []
        for s in range(seeds):
            tof = Vl53l0x(np.random.default_rng(s), "high_accuracy")
            c = TofConditions(plate_half_mm=half, background_mm=900, ambient="shaded_tube")
            cfg = dsp.tof_cfg(max_range_mm=800)
            for _ in range(20):
                mm, _ = dsp.tof_filter(cfg, tof.burst(500, c, 7))
                if mm is not None:
                    errs.append(mm - 500 - tof.offset_mm)
        plate[half * 2] = {
            "bias_mm": float(np.mean(errs)) if errs else None,
            "sd_mm": float(np.std(errs)) if errs else None,
            "readings": len(errs),
        }
    res["plate_size_at_500mm"] = plate
    save("e2_tof", res)
    return res


# --- E3: energy -------------------------------------------------------------------


def _year(args):
    ma, panel_w, seed, ntc = args
    w = power.Weather(seed)
    r = power.simulate_year(ma, w, power.Panel(p_stc_w=panel_w), charger=power.Tp4056(ntc_wired=ntc))
    return {
        "downtime_h": r.downtime_h,
        "min_soc": r.min_soc,
        "unsafe_charge_h": r.unsafe_charge_h,
        "warn_h": int(r.low_batt_warn.sum()),
        "soc": r.soc[::24].tolist(),
    }


def e3_energy(seeds: int) -> dict:
    cases = {
        "A wifi_mesh router (1 s)": esp32.total_ma(esp32.profile_ma("wifi_mesh", "router", esp32.ProfileCfg(1))),
        "A wifi_mesh leaf (1 s)": esp32.total_ma(esp32.profile_ma("wifi_mesh", "leaf", esp32.ProfileCfg(1))),
        "B lorawan (1 s)": esp32.total_ma(esp32.profile_ma("lorawan", "leaf", esp32.ProfileCfg(1))),
        "B lorawan (10 s)": esp32.total_ma(esp32.profile_ma("lorawan", "leaf", esp32.ProfileCfg(10))),
        "B lorawan (60 s)": esp32.total_ma(esp32.profile_ma("lorawan", "leaf", esp32.ProfileCfg(60))),
    }
    import sys

    sys.path.insert(0, str(REPO / "hardware"))
    import power_budget as pb  # the repo's existing ACHAL LoRa TDMA mesh budget

    # hardware/power_budget.py now includes the VL53L0X burst (7 high-accuracy shots per period)
    cases["ACHAL LoRa TDMA leaf (60 s) + ToF"] = pb.period_budget("leaf", 60).avg_ma
    cases["ACHAL LoRa TDMA relay (60 s) + ToF"] = pb.period_budget("relay", 60).avg_ma
    breakdown = {
        "A router 1 s": esp32.profile_ma("wifi_mesh", "router", esp32.ProfileCfg(1)),
        "A leaf 1 s": esp32.profile_ma("wifi_mesh", "leaf", esp32.ProfileCfg(1)),
        "B 1 s": esp32.profile_ma("lorawan", "leaf", esp32.ProfileCfg(1)),
        "B 60 s": esp32.profile_ma("lorawan", "leaf", esp32.ProfileCfg(60)),
    }
    jobs = [(ma, pw, s, False) for ma in cases.values() for pw in (2.0, 5.0) for s in range(seeds)]
    jobs += [(cases["ACHAL LoRa TDMA relay (60 s) + ToF"], 2.0, s, True) for s in range(seeds)]
    with ProcessPoolExecutor(min(4, os.cpu_count() or 1)) as ex:
        outs = list(ex.map(_year, jobs))
    table, k = {}, 0
    for name, ma in cases.items():
        for pw in (2.0, 5.0):
            rs = outs[k : k + seeds]
            k += seeds
            table[f"{name} | {pw:g} W"] = {
                "avg_ma": ma,
                "days_on_battery_alone": 3000 * 0.8 / (ma * 24),
                "downtime_h_per_year": ci95([r["downtime_h"] for r in rs]),
                "years_with_any_outage": float(np.mean([r["downtime_h"] > 0 for r in rs])),
                "min_soc_worst": float(min(r["min_soc"] for r in rs)),
                "charging_outside_0_50C_h": ci95([r["unsafe_charge_h"] for r in rs]),
                "_soc": np.mean([r["soc"] for r in rs], 0).tolist(),
            }
    rs = outs[k : k + seeds]
    table["ACHAL LoRa TDMA relay (60 s) + ToF | 2 W, NTC wired"] = {
        "downtime_h_per_year": ci95([r["downtime_h"] for r in rs]),
        "charging_outside_0_50C_h": ci95([r["unsafe_charge_h"] for r in rs]),
    }
    plt.figure(figsize=(6.4, 3.4))
    days = np.arange(365)
    for col, key in zip(
        C,
        [
            "A wifi_mesh router (1 s) | 2 W",
            "A wifi_mesh leaf (1 s) | 2 W",
            "B lorawan (1 s) | 2 W",
            "B lorawan (60 s) | 2 W",
            "ACHAL LoRa TDMA relay (60 s) + ToF | 2 W",
        ], strict=False,
    ):
        plt.plot(days, np.array(table[key]["_soc"]) * 100, color=col, lw=1.5, label=key.replace(" | 2 W", ""))
    plt.ylabel("battery state of charge (%), mean of years")
    plt.xlabel("day of year (NASA POWER Jharia climatology, 2 W panel)")
    plt.legend(fontsize=7, loc="lower left")
    fig("e3_energy_year")
    for v in table.values():
        v.pop("_soc", None)
    # Pi gateway: Pi 4 (~3.4 W) + SX1302 HAT (~1 W) + 4G modem (~1.5 W) running 24 h
    gw_wh = (3.4 + 1.0 + 1.5) * 24
    res = {
        "profiles_ma": breakdown,
        "cases": table,
        "regulator": {
            "ht7333_max_ma": 250,
            "esp32_wifi_tx_peak_ma": 240 + 40,
            "note": "Wi-Fi TX bursts (~280 mA with CPU) exceed the HT7333's 250 mA: options A/C need a >=500 mA LDO",
        },
        "pi_gateway_sizing": {
            "daily_wh": gw_wh,
            "panel_w_for_worst_month": gw_wh / (min(power.NASA["ALLSKY_SFC_SW_DWN"]) * 0.6),
            "battery_ah_12v_3_days": gw_wh * 3 / 12 / 0.5,
        },
    }
    save("e3_energy", res)
    return res


# --- E4: network ----------------------------------------------------------------


def _net(args):
    arch, n, sp, iv, seed, dur, kw = args
    xy = grid(n, sp)
    dl = N.simulate(arch, xy, iv, dur, seed, **kw)
    m = N.metrics(dl, n, iv, dur)
    return {
        "arch": arch,
        "n": n,
        "spacing": sp,
        "interval": iv,
        "seed": seed,
        **{k: v for k, v in m.items() if k != "losses"},
        "losses": m["losses"],
    }


def e4_network(seeds: int) -> dict:
    jobs = []
    for arch in ("wifi_mesh", "lorawan"):
        for sp in (5, 10, 20, 40, 60):
            jobs += [(arch, 20, sp, 1.0, s, 120.0, {}) for s in range(seeds)]
        for n in (10, 50, 100):
            jobs += [(arch, n, 10, 1.0, s, 120.0, {}) for s in range(seeds)]
        for iv in (10.0, 60.0):
            jobs += [(arch, 20, 10, iv, s, 60 * iv, {}) for s in range(seeds)]
        jobs += [(arch, 100, 40, 1.0, s, 120.0, {}) for s in range(seeds)]
    with ProcessPoolExecutor(min(4, os.cpu_count() or 1)) as ex:
        rows = list(ex.map(_net, jobs))
    agg: dict = {}
    for r in rows:
        key = f"{r['arch']} n={r['n']} spacing={r['spacing']} interval={r['interval']:g}"
        agg.setdefault(key, []).append(r)
    summary = {
        k: {
            "pdr": ci95([r["pdr"] for r in v]),
            "delay_ms_p50": ci95([r["delay_ms_p50"] for r in v]),
            "delay_ms_p95": ci95([r["delay_ms_p95"] for r in v]),
            "mean_hops": ci95([r["mean_hops"] for r in v]),
            "max_hops": max(r["max_hops"] for r in v),
            "worst_node_pdr": ci95([r["worst_node_pdr"] for r in v]),
        }
        for k, v in agg.items()
    }
    # LoRaWAN DES against pure-ALOHA theory (capture off): S = exp(-2G)
    theory = []
    for iv in (0.5, 1.0, 2.0, 5.0, 10.0, 30.0):
        xy = grid(20, 10)
        # Periodic senders with fixed phases: one seed is one draw of phases, so average 8 of them.
        pdrs = [
            N.metrics(N.simulate("lorawan", xy, iv, 300 * iv, s, lora_kw={"capture_db": None}), 20, iv, 300 * iv)["pdr"]
            for s in range(1, 9)
        ]
        topo = N.Topology(xy, (-10.0, float(xy[:, 1].mean())), N.lora_radio(7), 3)
        G = N.LoRaWanStar(topo, np.random.default_rng(0)).offered_load(iv)
        theory.append(
            {
                "interval_s": iv,
                "G": G,
                "sim_pdr": float(np.mean(pdrs)),
                "sim_pdr_ci95": ci95(pdrs),
                "aloha_exp_minus_2G": float(np.exp(-2 * G)),
            }
        )
    # Backhaul (option C)
    bh = {
        d: {
            "rx_dbm": N.Backhaul(distance_km=d).rx_dbm(),
            "sf": N.Backhaul(distance_km=d).pick_sf(),
            "util_raw_1s_20n": N.Backhaul(distance_km=d).utilisation_raw(20, 1.0),
            "util_summaries_20n": N.Backhaul(distance_km=d).utilisation_summary(20),
            "event_latency_s_p50": float(
                np.nanmedian(
                    [
                        x if x is not None else np.nan
                        for x in [
                            N.Backhaul(distance_km=d).event_latency_s(np.random.default_rng(s))[0] for s in range(200)
                        ]
                    ]
                )
            ),
        }
        for d in (2, 3, 5, 7, 10)
    }
    [
        (sp, summary[f"{a} n=20 spacing={sp} interval=1"]) for a in ("wifi_mesh",) for sp in (5, 10, 20, 40, 60)
    ]
    plt.figure(figsize=(6.4, 3.0))
    ivs = [t["interval_s"] for t in theory]
    plt.plot(ivs, [t["aloha_exp_minus_2G"] for t in theory], color=C[1], label="pure ALOHA theory, e^(-2G)")
    plt.plot(
        ivs,
        [t["sim_pdr"] for t in theory],
        color=C[0],
        ls="none",
        marker="o",
        ms=6,
        label="this simulator (capture off)",
    )
    lw = [summary.get(f"lorawan n=20 spacing=10 interval={iv:g}") for iv in (1.0, 10.0, 60.0)]
    plt.plot([1, 10, 60], [x["pdr"][0] for x in lw], color=C[2], marker="s", ms=6, lw=1.5, label="with 6 dB capture")
    plt.xscale("log")
    plt.xlabel("reporting interval per node (s, log), 20 nodes, SF7, 3 channels")
    plt.ylabel("packet delivery ratio")
    plt.legend(loc="lower right")
    fig("e4_lorawan_aloha")
    plt.figure(figsize=(6.4, 3.0))
    for k, n in enumerate((20, 100)):
        sps = (5, 10, 20, 40, 60) if n == 20 else (10, 40)
        ys = [summary[f"wifi_mesh n={n} spacing={sp} interval=1"]["delay_ms_p95"][0] for sp in sps]
        plt.plot(sps, ys, marker="o", ms=5, color=C[k], label=f"Wi-Fi mesh, {n} nodes: p95 delay")
    plt.xlabel("node spacing (m)")
    plt.ylabel("end-to-end delay p95 (ms)")
    plt.legend(loc="upper left")
    fig("e4_wifi_delay")
    res = {"summary": summary, "lorawan_vs_aloha": theory, "backhaul": bh, "seeds": seeds}
    save("e4_network", res)
    return res


# --- E5: ns-3 cross-check ----------------------------------------------------------


def e5_ns3(seeds: int) -> dict:
    """Compare the Python Wi-Fi link/MAC abstraction with ns-3 (ad-hoc, single hop, same propagation)."""
    binary = os.environ.get("ACHAL_NS3_BIN") or shutil.which("achal-wifi-star")
    rows = []
    for sp, iv in ((10, 1.0), (40, 1.0), (80, 1.0), (120, 1.0), (160, 1.0), (200, 1.0), (10, 0.05), (10, 0.01)):
        py = []
        for s in range(seeds):
            xy = grid(20, sp)
            ideal = N.Radio(
                2.437e9,
                tx_dbm=17.0,
                sens_dbm=-93.0,
                h_tx_m=1.0,
                h_rx_m=1.0,
                sigma_sh_db=0.0,
                sigma_f_db=0.0,
                clutter_db=6.0,
                excess_db_per_m=0.0,
            )
            dl = N.simulate(
                "wifi_mesh",
                xy,
                iv,
                60.0 if iv >= 1 else 10.0,
                s,
                radio=ideal,
                wifi_kw={"max_layer": 1, "parent_min_dbm": -200.0, "pi_parse_s": (0.0, 0.0), "hop_sw_s": (0.0, 0.0)},
            )
            py.append(N.metrics(dl, 20, iv, 60.0 if iv >= 1 else 10.0))
        row = {
            "spacing": sp,
            "interval_s": iv,
            "py_pdr": ci95([m["pdr"] for m in py]),
            "py_delay_ms_p50": ci95([m["delay_ms_p50"] for m in py]),
        }
        if binary:
            ns = []
            for s in range(1, seeds + 1):
                out = (
                    subprocess.run(
                        [
                            binary,
                            "--n=20",
                            f"--spacing={sp}",
                            f"--interval={iv}",
                            f"--time={60 if iv >= 1 else 10}",
                            f"--seed={s}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )
                    .stdout.strip()
                    .splitlines()[-1]
                )
                f = out.split(",")
                ns.append((float(f[6]), float(f[7])))
            row["ns3_pdr"] = ci95([a for a, _ in ns])
            row["ns3_flow_delay_ms_median"] = ci95([b for _, b in ns])
        rows.append(row)
    res = {
        "ns3_binary": bool(binary),
        "rows": rows,
        "python_settings": "shadowing and fading set to 0 and excess loss 0 for this comparison",
        "note": "Pi parse time and per-hop software delay are off in the Python model here, "
        "so both delays are MAC + PHY only",
    }
    save("e5_ns3", res)
    return res


# --- E6: the guide's first experiment, end to end ---------------------------------


def _e2e(args):
    from .e2e import run

    sc, arch, seed = args
    m = run(sc, arch, seed=seed)
    return {
        "scenario": sc,
        "arch": arch,
        "seed": seed,
        "network": m["network"],
        "detection": m["detection"],
        "timestamp_error_ms": m["timestamp_error_ms"],
        "energy": m["energy"],
    }


def e6_first_experiment(seeds: int) -> dict:
    jobs = [
        (sc, arch, s)
        for sc in ("normal", "warning", "critical")
        for arch in ("wifi_mesh", "lorawan", "hybrid")
        for s in range(1, seeds + 1)
    ]
    with ProcessPoolExecutor(min(4, os.cpu_count() or 1)) as ex:
        rows = list(ex.map(_e2e, jobs))
    table = {}
    for sc in ("normal", "warning", "critical"):
        for arch in ("wifi_mesh", "lorawan", "hybrid"):
            rs = [r for r in rows if r["scenario"] == sc and r["arch"] == arch]
            d = [r["detection"] for r in rs]
            ent = {
                "pdr": ci95([r["network"]["pdr"] for r in rs]),
                "delay_ms_p50": ci95([r["network"]["delay_ms_p50"] for r in rs]),
                "delay_ms_p95": ci95([r["network"]["delay_ms_p95"] for r in rs]),
                "fa_nodes_guard_0.5": int(sum(x["false_alarms"]["guard_0.5"]["false_alarm_nodes"] for x in d)),
                "clear_nodes_guard_0.5": int(sum(x["false_alarms"]["guard_0.5"]["clear_nodes"] for x in d)),
                "fa_nodes_guard_0.8": int(sum(x["false_alarms"]["guard_0.8"]["false_alarm_nodes"] for x in d)),
                "clear_nodes_guard_0.8": int(sum(x["false_alarms"]["guard_0.8"]["clear_nodes"] for x in d)),
                "ts_err_ms_p95": ci95([r["timestamp_error_ms"]["p95"] for r in rs]),
            }
            for lvl in ("1", "2"):
                tot = sum(x["per_level"][lvl]["nodes_truly_at_level"] for x in d)
                hit = sum(x["per_level"][lvl]["nodes_alerted"] for x in d)
                lat = [
                    x["per_level"][lvl]["latency_s_median"]
                    for x in d
                    if x["per_level"][lvl]["latency_s_median"] is not None
                ]
                ent[f"level{lvl}"] = {
                    "nodes": tot,
                    "alerted": hit,
                    "recall": hit / tot if tot else None,
                    "latency_s_median_of_runs": float(np.median(lat)) if lat else None,
                }
            v = [x["vibration"] for x in d]
            ent["vibration"] = {k: int(sum(x[k] for x in v)) for k in v[0]}
            table[f"{sc} | {arch}"] = ent
    res = {"seeds": seeds, "table": table, "runs": rows}
    save("e6_first_experiment", res)
    return res


EXPS = {"e1": e1_mpu, "e2": e2_tof, "e3": e3_energy, "e4": e4_network, "e5": e5_ns3, "e6": e6_first_experiment}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=list(EXPS))
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    seeds = {"e1": 20, "e2": 10, "e3": 10, "e4": 5, "e5": 3, "e6": 5}
    for k in a.only:
        s = 2 if a.quick else seeds[k]
        print(f"[{k}] seeds={s} ...", flush=True)
        r = EXPS[k](s)
        print(json.dumps({kk: vv for kk, vv in r.items() if kk not in ("runs",)}, default=float)[:1500], flush=True)


if __name__ == "__main__":
    main()
