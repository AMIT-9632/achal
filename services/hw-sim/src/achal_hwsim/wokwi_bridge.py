# SPDX-License-Identifier: MIT
"""Feed the Wokwi node's serial output to the Pi gateway logic (and optionally MQTT).

    wokwi-cli firmware/wokwi --scenario tilt-alarm.test.yaml --serial-log-file wokwi.log
    python -m achal_hwsim.wokwi_bridge wokwi.log [--mqtt localhost:1883]
    # or live: wokwi-cli ... | python -m achal_hwsim.wokwi_bridge -

Each JSON line from firmware/src/wokwi-node is one packet. The bridge stamps the
gateway receive time with the node's own ts_ms (the serial link has no radio
delay), runs the same Gateway class as the network simulation, and prints the
events it raises.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

from .gateway import Gateway


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", help="serial log file, or - for stdin")
    ap.add_argument("--mqtt", default=None, help="host:port to publish packets and events to")
    ap.add_argument("--site", default="wokwi")
    a = ap.parse_args(argv)
    pub = None
    if a.mqtt:
        import paho.mqtt.client as mqtt

        host, port = a.mqtt.split(":")
        pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="achal-wokwi-bridge")
        pub.connect(host, int(port))
        pub.loop_start()
    gw = Gateway(["w01"], np.array([[0.0, 0.0]]), interval_s=1.0, offline_after=15.0)
    src = sys.stdin if a.log == "-" else open(a.log, encoding="utf-8", errors="replace")
    n = 0
    for line in src:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "seq" not in p:
            print(f"# {line}")
            continue
        n += 1
        p.setdefault("batt_mv", 4000)
        t = p["ts_ms"] / 1000.0
        p["t_true"] = t
        evs = gw.ingest(p, t)
        if pub:
            pub.publish(f"achal/{a.site}/hwsim/telemetry/{p['node']}", json.dumps(p))
        for e in evs:
            print(json.dumps(e.as_dict()))
            if pub:
                pub.publish(f"achal/{a.site}/hwsim/events", json.dumps(e.as_dict()))
    print(f"# {n} packets, {len(gw.events)} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
