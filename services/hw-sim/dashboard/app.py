# SPDX-License-Identifier: MIT
"""ACHAL hardware-simulation dashboard (simulation guide, stage 4).

    streamlit run services/hw-sim/dashboard/app.py                     # replay runs
    streamlit run services/hw-sim/dashboard/app.py -- --live live.json  # follow mqtt_link gateway

Replay mode loads a run written by `python -m achal_hwsim.e2e --out runs/<name>`
(or simulates one from the sidebar) and lets you scrub or play it: node schematic,
tilt / displacement / vibration trends, battery, last-seen, link status, alerts
and the event history, with the ground truth alongside for checking.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
RUNS = HERE.parents[2] / "data" / "hwsim" / "runs"
LEVEL_COLOR = {"normal": "#1baf7a", "warning": "#fab219", "critical": "#e34948", "offline": "#9a9893"}
EVENT_ICON = {
    "ground_critical": "🔴",
    "ground_warning": "🟠",
    "vibration_advisory": "〰️",
    "node_offline": "⚫",
    "node_online": "🟢",
    "low_battery": "🔋",
    "sensor_fault": "⚠️",
    "cleared": "✅",
}

st.set_page_config(page_title="ACHAL hardware simulation", layout="wide")


def live_path() -> str | None:
    if "--live" in sys.argv:
        return sys.argv[sys.argv.index("--live") + 1]
    return None


@st.cache_data(show_spinner=False)
def load_run(path: str) -> dict:
    p = Path(path)
    return {
        "packets": pd.read_csv(p / "packets.csv.gz"),
        "rx": pd.read_csv(p / "deliveries.csv.gz"),
        "snaps": pd.read_csv(p / "snapshots.csv.gz"),
        "events": pd.read_json(p / "events.json"),
        "nodes": pd.read_csv(p / "nodes.csv"),
        "metrics": json.loads((p / "metrics.json").read_text()),
    }


def status_of(row) -> str:
    if not row["online"]:
        return "offline"
    return {0: "normal", 1: "warning", 2: "critical"}[int(row["level"])]


def node_map(snap: pd.DataFrame, gw_xy) -> alt.Chart:
    s = snap.copy()
    s["status"] = s.apply(status_of, axis=1)
    s["batt_v"] = s["batt_mv"] / 1000
    pts = (
        alt.Chart(s)
        .mark_circle(size=260, stroke="#fcfcfb", strokeWidth=2)
        .encode(
            x=alt.X("x:Q", title="east (m)"),
            y=alt.Y("y:Q", title="north (m)"),
            color=alt.Color(
                "status:N",
                scale=alt.Scale(domain=list(LEVEL_COLOR), range=list(LEVEL_COLOR.values())),
                legend=alt.Legend(title="node status", orient="top"),
            ),
            tooltip=[
                "node",
                "status",
                alt.Tooltip("tilt_deg:Q", format=".3f"),
                alt.Tooltip("disp_mm:Q", format=".1f"),
                alt.Tooltip("ppv_mm_s:Q", format=".1f"),
                alt.Tooltip("batt_v:Q", format=".2f"),
                alt.Tooltip("last_seen_age_s:Q", format=".0f"),
                alt.Tooltip("rssi_dbm:Q", format=".0f"),
                "hops",
            ],
        )
    )
    lab = alt.Chart(s).mark_text(dy=-16, fontSize=10, color="#52514e").encode(x="x:Q", y="y:Q", text="node")
    gw = (
        alt.Chart(pd.DataFrame({"x": [gw_xy[0]], "y": [gw_xy[1]], "name": ["Pi gateway"]}))
        .mark_point(shape="square", size=220, filled=True, color="#2a78d6")
        .encode(x="x:Q", y="y:Q", tooltip=["name"])
    )
    gwl = (
        alt.Chart(pd.DataFrame({"x": [gw_xy[0]], "y": [gw_xy[1]], "t": ["Pi"]}))
        .mark_text(dy=-16)
        .encode(x="x:Q", y="y:Q", text="t")
    )
    return (pts + lab + gw + gwl).properties(height=340)


def trend(df: pd.DataFrame, y: str, title: str, truth: str | None = None, rule: list[float] | None = None) -> alt.Chart:
    base = alt.Chart(df).encode(x=alt.X("t_true:Q", title="time (s)"))
    layers = [
        base.mark_line(color="#2a78d6", strokeWidth=2).encode(
            y=alt.Y(f"{y}:Q", title=title),
            tooltip=[alt.Tooltip("t_true:Q", format=".0f"), alt.Tooltip(f"{y}:Q", format=".3f")],
        )
    ]
    if truth:
        layers.append(base.mark_line(color="#eb6834", strokeDash=[4, 3], strokeWidth=1.5).encode(y=f"{truth}:Q"))
    for r in rule or []:
        layers.append(alt.Chart(pd.DataFrame({"r": [r]})).mark_rule(color="#9a9893", strokeDash=[2, 2]).encode(y="r:Q"))
    return alt.layer(*layers).properties(height=170)


def replay_page() -> None:
    st.sidebar.header("Run")
    RUNS.mkdir(parents=True, exist_ok=True)
    runs = sorted(p.name for p in RUNS.iterdir() if (p / "metrics.json").exists())
    with st.sidebar.expander("Simulate a new run", expanded=not runs):
        sc = st.selectbox("scenario", ["critical", "warning", "normal"])
        arch = st.selectbox(
            "network",
            ["wifi_mesh", "lorawan", "hybrid"],
            format_func=lambda a: {
                "wifi_mesh": "A: Wi-Fi mesh",
                "lorawan": "B: LoRaWAN",
                "hybrid": "C: mesh + LoRa uplink",
            }[a],
        )
        n = st.slider("nodes", 10, 100, 20, 5)
        spacing = st.slider("spacing (m)", 5, 60, 10, 5)
        interval = st.select_slider("reporting interval (s)", [1, 2, 5, 10], 1)
        seed = st.number_input("seed", 1, 999, 1)
        fails = st.checkbox("inject failures (lost packets, offline node, gateway outage, low battery)", True)
        if st.button("Run simulation", type="primary"):
            from achal_hwsim.e2e import run

            name = f"{sc}-{arch}-n{n}-s{spacing}-i{interval}-seed{seed}"
            with st.spinner("simulating nodes, network and gateway..."):
                run(sc, arch, n, float(spacing), float(interval), 1800.0, int(seed), fails, out=RUNS / name)
            st.session_state["run"] = name
            st.rerun()
    if not runs:
        st.info(
            "No runs yet. Simulate one from the sidebar, or `python -m achal_hwsim.e2e --out services/hw-sim/runs/x`."
        )
        return
    name = st.sidebar.selectbox(
        "replay",
        runs,
        index=runs.index(st.session_state.get("run", runs[0])) if st.session_state.get("run") in runs else 0,
    )
    R = load_run(str(RUNS / name))
    m = R["metrics"]
    cfg = m["config"]
    times = [x for x in sorted(R["snaps"].t.unique()) if x <= cfg["duration_s"]]
    if "tidx" not in st.session_state or st.session_state.get("tidx_run") != name:
        st.session_state.tidx, st.session_state.tidx_run = len(times) - 1, name
    c1, c2, c3 = st.sidebar.columns(3)
    playing = c1.toggle("▶ play", False)
    if c2.button("⏮"):
        st.session_state.tidx = 0
    if c3.button("⏭"):
        st.session_state.tidx = len(times) - 1
    st.session_state.tidx = st.sidebar.slider(
        "time", 0, len(times) - 1, st.session_state.tidx, format="%d", help="10 s steps"
    )
    t = times[st.session_state.tidx]

    st.title("ACHAL · hardware simulation")
    st.caption(
        f"{cfg['scenario']} scenario · {cfg['arch']} · {cfg['n']} nodes at {cfg['spacing_m']:g} m · "
        f"{cfg['interval_s']:g} s reporting · seed {cfg['seed']} · t = {t:.0f} s. "
        "Synthetic data; thresholds illustrative; not a certified warning system."
    )
    snap = R["snaps"][R["snaps"].t == t]
    ev = R["events"]
    ev_now = ev[ev.t <= t] if len(ev) else ev
    k = st.columns(6)
    k[0].metric("critical nodes", int((snap.level == 2).sum()))
    k[1].metric("warning nodes", int((snap.level == 1).sum()))
    k[2].metric("offline", int((~snap.online).sum()))
    k[3].metric("low battery", int(((snap["flags"].fillna(0).astype(int) & 4) > 0).sum()))
    pdr = m["network"]["pdr"]
    k[4].metric("packet delivery", f"{pdr:.1%}")
    k[5].metric("delay p95", f"{m['network']['delay_ms_p95']:.0f} ms")

    left, right = st.columns([3, 2])
    with left:
        gw_xy = (R["nodes"].x.min() - 10, R["nodes"].y.mean())
        st.altair_chart(node_map(snap, gw_xy), use_container_width=True)
    with right:
        st.subheader("Alerts and events")
        if len(ev_now):
            show = ev_now.sort_values("t", ascending=False).head(14)
            for e in show.itertuples():
                st.markdown(
                    f"{EVENT_ICON.get(e.kind, '•')} **{e.t:.0f} s** · `{e.node}` · {e.kind.replace('_', ' ')}"
                    f"{' · ' + e.detail if e.detail else ''}"
                )
        else:
            st.write("No events yet.")

    st.subheader("Node detail")
    node = st.selectbox(
        "node",
        list(R["nodes"].node),
        index=int(snap.sort_values("level", ascending=False).index[0] - snap.index[0]) if len(snap) else 0,
    )
    st.caption(
        "Blue: what the node measured and sent. Orange dashed: simulation ground truth. Grey: thresholds "
        "(tilt 0.10/0.30°, displacement 5/15 mm, DGMS PPV 5 mm/s below 8 Hz, battery warn 3.45 V / off 3.30 V)."
    )
    pk = R["packets"]
    d = pk[(pk.node == node) & (pk.t_true <= t)].copy()
    d["tilt_deg_meas"] = (d.tilt_x_deg**2 + d.tilt_y_deg**2) ** 0.5
    d["batt_v"] = d.batt_mv / 1000
    g1, g2 = st.columns(2)
    g1.altair_chart(trend(d, "tilt_deg_meas", "tilt change (°)", "tilt_deg", [0.1, 0.3]), use_container_width=True)
    g2.altair_chart(
        trend(d.dropna(subset=["disp_mm"]), "disp_mm", "displacement (mm)", "crack_mm", [5, 15]),
        use_container_width=True,
    )
    g3, g4 = st.columns(2)
    g3.altair_chart(trend(d, "ppv_mm_s", "PPV (mm/s)", None, [5]), use_container_width=True)
    g4.altair_chart(trend(d, "batt_v", "battery (V)", None, [3.45, 3.30]), use_container_width=True)

    st.subheader("Node health")
    h = snap[["node", "online", "level", "last_seen_age_s", "batt_mv", "rssi_dbm", "hops", "received", "flags"]].copy()
    h["status"] = snap.apply(status_of, axis=1)
    st.dataframe(h.set_index("node"), use_container_width=True, height=260)

    with st.expander("Network and detection metrics (whole run)"):
        st.json(
            {
                "network": m["network"],
                "detection": m["detection"],
                "timestamp_error_ms": m["timestamp_error_ms"],
                "energy": m["energy"],
                "failures injected": cfg["failures"],
            }
        )
    if playing and st.session_state.tidx < len(times) - 1:
        time.sleep(0.4)
        st.session_state.tidx += 1
        st.rerun()


def live_page(path: str) -> None:
    st.title("ACHAL · live gateway (MQTT)")
    p = Path(path)
    if not p.exists():
        st.info(f"Waiting for {p} from `python -m achal_hwsim.mqtt_link gateway`...")
    else:
        s = json.loads(p.read_text())
        snap = pd.DataFrame(s["nodes"])
        st.caption(f"gateway time {s['t']:.0f} s · {len(snap)} nodes")
        st.altair_chart(node_map(snap, (snap.x.min() - 10, snap.y.mean())), use_container_width=True)
        for e in reversed(s["events"][-15:]):
            st.markdown(
                f"{EVENT_ICON.get(e['kind'], '•')} **{e['t']:.0f} s** · `{e['node']}` · {e['kind']} {e['detail']}"
            )
    time.sleep(1.0)
    st.rerun()


lp = live_path()
if lp:
    live_page(lp)
else:
    replay_page()
