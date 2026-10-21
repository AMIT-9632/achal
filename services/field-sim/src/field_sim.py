# SPDX-License-Identifier: MIT
"""Field simulator: virtual ESP32 surface nodes over a Jharia-type panel.

SCAFFOLD STUB (W0). Emits flat-ground readings with sensor noise and sends each
one straight to the gateway over UDP, which stands in for the LoRa radio.

W1 replaces `ground_state` with the Knothe influence-function trough, pillar
collapse, drift and faults (scenarios: normal, trough, pillar_collapse, faults, fire).
W2 replaces `send_via_mesh` with multi-hop routing, packet loss and self-healing.
"""

from __future__ import annotations

import logging
import os
import random
import socket
import time
from datetime import UTC, datetime, timedelta

from achal_common import Telemetry, load_site

log = logging.getLogger("field-sim")


def ground_state(node: str, t_s: float, rng: random.Random) -> dict:
    """Return sensor readings for one node at simulated time t_s. Flat ground only."""
    return {
        "tilt_x_deg": rng.gauss(0, 0.002),
        "tilt_y_deg": rng.gauss(0, 0.002),
        "vib_rms_g": abs(rng.gauss(0.0005, 0.0002)),
        "crack_mm": rng.gauss(0, 0.02),
        "ext_mm": rng.gauss(0, 0.05),
        "temp_c": 28 + 6 * rng.random(),
        "batt_v": 3.95,
        "truth": {"subsidence_mm": 0.0, "event": "none"},
    }


def send_via_mesh(sock: socket.socket, target: tuple[str, int], reading: Telemetry) -> None:
    """Single hop, no loss. W2 owns the real mesh model."""
    reading.hops = 1
    reading.path = [reading.node]
    reading.rssi_dbm = -95.0
    sock.sendto(reading.model_dump_json(exclude_none=True).encode(), target)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    site = load_site()
    speed = float(os.environ.get("SIM_SPEED", "60"))
    rng = random.Random(int(os.environ.get("SIM_SEED", "42")))
    target = (os.environ.get("GATEWAY_HOST", "localhost"), int(os.environ.get("GATEWAY_UDP_PORT", "1700")))
    period = site.mesh.sample_period_s
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start = datetime.now(UTC)
    log.info("site=%s nodes=%d scenario=%s speed=x%.0f -> %s:%d", site.site_id, len(site.node_ids()),
             os.environ.get("SCENARIO", "normal"), speed, *target)

    seq = 0
    while True:
        t_s = seq * period
        ts = start + timedelta(seconds=t_s)
        for node in site.node_ids():
            reading = Telemetry(site=site.site_id, node=node, seq=seq, ts=ts, **ground_state(node, t_s, rng))
            send_via_mesh(sock, target, reading)
        seq += 1
        time.sleep(period / speed)


if __name__ == "__main__":
    main()
