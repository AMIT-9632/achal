# SPDX-License-Identifier: MIT
"""Stage 3 message interface: virtual nodes -> MQTT -> Pi gateway, as separate processes.

    # 1. any MQTT broker, e.g. the repo's Mosquitto: docker compose up -d mosquitto
    # 2. gateway: subscribes, runs the Gateway logic, publishes events, keeps a state file
    python -m achal_hwsim.mqtt_link gateway --broker localhost:1883 --run runs/critical --state live.json
    # 3. nodes: replay a simulated run's delivered packets at their network arrival times
    python -m achal_hwsim.mqtt_link nodes --broker localhost:1883 --run runs/critical --speed 10
    # 4. dashboard, live page: streamlit run services/hw-sim/dashboard/app.py -- --live live.json

Topics (QoS 1): achal/{site}/hwsim/telemetry/{node} (one packet, JSON),
achal/{site}/hwsim/events (gateway events), achal/{site}/hwsim/state (retained snapshot).
The packet JSON is the node packet of achal_hwsim.node, without the simulation-only truth.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .gateway import Gateway


def _client(broker: str, cid: str):
    import paho.mqtt.client as mqtt

    host, port = broker.split(":")
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    c.connect(host, int(port))
    return c


def nodes(a) -> None:
    run = Path(a.run)
    pk = pd.read_csv(run / "packets.csv.gz")
    rx = pd.read_csv(run / "deliveries.csv.gz").sort_values("t_rx")
    pk = pk.set_index(["node", "seq"])
    truth_cols = [c for c in ("tilt_deg", "crack_mm", "label", "vib_exceed") if c in pk.columns]
    c = _client(a.broker, "achal-hwsim-nodes")
    c.loop_start()
    t0_wall, t0 = time.time(), float(rx.t_rx.iloc[0])
    for r in rx.itertuples():
        wait = (r.t_rx - t0) / a.speed - (time.time() - t0_wall)
        if wait > 0:
            time.sleep(wait)
        p = pk.loc[(r.node, r.seq)].drop(labels=truth_cols).to_dict()
        p = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in p.items()}
        p.update(node=r.node, seq=int(r.seq), rx_hops=int(r.hops), rx_rssi_dbm=r.rssi_dbm, t_rx_sim=r.t_rx)
        c.publish(f"achal/{a.site}/hwsim/telemetry/{r.node}", json.dumps(p, default=float), qos=1)
    c.loop_stop()


def gateway(a) -> None:
    run = Path(a.run)
    nd = pd.read_csv(run / "nodes.csv")
    interval = json.loads((run / "metrics.json").read_text())["config"]["interval_s"]
    gw = Gateway(list(nd.node), nd[["x", "y"]].to_numpy(), interval)
    c = _client(a.broker, "achal-hwsim-gateway")
    state = Path(a.state)
    last = [0.0]

    def on_message(cl, _ud, msg):
        p = json.loads(msg.payload)
        t = p.get("t_rx_sim", time.time())
        evs = gw.ingest(p, t, p.get("rx_rssi_dbm"), p.get("rx_hops", 0)) + gw.tick(t)
        for e in evs:
            cl.publish(f"achal/{a.site}/hwsim/events", json.dumps(e.as_dict(), default=float), qos=1)
        if time.time() - last[0] > 0.5:
            snap = {"t": t, "nodes": gw.snapshot(t), "events": [e.as_dict() for e in gw.events[-200:]]}
            state.write_text(json.dumps(snap, default=float))
            cl.publish(f"achal/{a.site}/hwsim/state", json.dumps(snap, default=float), qos=1, retain=True)
            last[0] = time.time()

    c.on_message = on_message
    c.subscribe(f"achal/{a.site}/hwsim/telemetry/+", qos=1)
    c.loop_forever()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("nodes", "gateway"):
        s = sub.add_parser(name)
        s.add_argument("--broker", default="localhost:1883")
        s.add_argument("--run", required=True, help="output directory of achal_hwsim.e2e --out")
        s.add_argument("--site", default="jharia-hwsim")
        if name == "nodes":
            s.add_argument("--speed", type=float, default=10.0, help="replay speed-up")
        else:
            s.add_argument("--state", default="live.json")
    a = ap.parse_args(argv)
    {"nodes": nodes, "gateway": gateway}[a.cmd](a)


if __name__ == "__main__":
    main()
