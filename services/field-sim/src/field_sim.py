# SPDX-License-Identifier: MIT
"""Field simulator: virtual ESP32 surface nodes over a Jharia-type panel.

Ground physics, sensors and the five scenarios live in the `achal_sim` package (W1).
This entry point steps the scenario in accelerated real time and hands each reading
to `send_via_mesh`, which stands in for the LoRa radio (W2 owns the mesh model).

Environment: SCENARIO (normal | trough | pillar_collapse | faults | fire), SIM_SEED,
SIM_SPEED (simulated seconds per real second), SIM_PERIOD_S (sample period, default
from the site config), SIM_START (ISO time of t=0, default now).
"""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import UTC, datetime

from achal_common import Telemetry, load_site
from achal_sim import FieldSimulator

log = logging.getLogger("field-sim")


_mesh = None


def send_via_mesh(sock: socket.socket, target: tuple[str, int], reading: Telemetry) -> None:
    """Route the reading over the simulated LoRa mesh (mesh.py) and send whatever reaches the gateway:
    nothing if it was lost, one frame, or two if a lost ACK caused a retransmission."""
    global _mesh
    if _mesh is None:
        from mesh import mesh_from_env

        _mesh = mesh_from_env(load_site(reading.site))
        log.info("mesh mode=%s nodes down=%d at t=%.0fs", _mesh.mode, len(_mesh.failing), _mesh.fail_at_s)
    for frame in _mesh.transmit(reading):
        sock.sendto(frame.model_dump_json(exclude_none=True).encode(), target)
    stats = _mesh.stats
    if stats.generated and stats.generated % (len(_mesh.nodes) * 60) == 0:
        log.info("mesh %s", stats.summary())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    site = load_site()
    scenario = os.environ.get("SCENARIO", "normal")
    speed = float(os.environ.get("SIM_SPEED", "60"))
    seed = int(os.environ.get("SIM_SEED", "42"))
    period = float(os.environ.get("SIM_PERIOD_S") or site.mesh.sample_period_s)
    start_env = os.environ.get("SIM_START")
    start = datetime.fromisoformat(start_env) if start_env else datetime.now(UTC).replace(microsecond=0)
    target = (os.environ.get("GATEWAY_HOST", "localhost"), int(os.environ.get("GATEWAY_UDP_PORT", "1700")))
    sim = FieldSimulator(site, scenario, seed, start, period)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log.info("site=%s nodes=%d scenario=%s seed=%d speed=x%.0f period=%.0fs -> %s:%d", site.site_id,
             len(sim.nodes), scenario, seed, speed, period, *target)
    log.info("plan: onset_h=%s failure_h=%s %s", sim.plan.onset_h, sim.plan.failure_h, sim.plan.params)

    while True:
        tick = time.monotonic()
        for msg in sim.step():
            send_via_mesh(sock, target, Telemetry.model_validate(msg))
        time.sleep(max(0.0, period / speed - (time.monotonic() - tick)))


if __name__ == "__main__":
    main()
