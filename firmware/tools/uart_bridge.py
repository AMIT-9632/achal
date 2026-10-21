# SPDX-License-Identifier: MIT
"""Connects the LoRa gateway radio (firmware/src/gateway-lora) to services/gateway.

Runs on the Raspberry Pi next to the gateway service. With real hardware the
radio replaces field-sim: telemetry JSON lines from the serial port are sent to
the gateway's UDP port exactly as field-sim sends them, so nothing downstream
changes. Siren commands published on MQTT are passed down to the radio, which
puts them in the next beacon, and the radio clock is kept in sync.

    python firmware/tools/uart_bridge.py --port /dev/ttyUSB0 --gateway 127.0.0.1:1700

Needs pyserial (BSD-3) and achal-common.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
import time

log = logging.getLogger("uart_bridge")

LEVELS = {"normal": 0, "watch": 1, "warning": 2, "critical": 3}


class SirenState:
    """Folds per-siren MQTT commands into the mask + level the beacon carries."""

    def __init__(self, siren_ids: list[str]) -> None:
        self.index = {sid: i for i, sid in enumerate(siren_ids)}
        self.on: dict[str, bool] = {}
        self.level: dict[str, int] = {}

    def apply(self, siren_id: str, cmd: dict) -> str | None:
        if siren_id not in self.index:
            return None
        self.on[siren_id] = bool(cmd.get("on"))
        self.level[siren_id] = LEVELS.get(str(cmd.get("level", "normal")), 0)
        mask = sum(1 << self.index[s] for s, on in self.on.items() if on)
        level = max((lvl for s, lvl in self.level.items() if self.on.get(s)), default=0)
        return f"S {mask} {level}\n"


def route_line(line: str) -> bytes | None:
    """A JSON line from the radio becomes one UDP datagram; log lines are dropped."""
    line = line.strip()
    if not line.startswith("{"):
        if line:
            log.info("radio: %s", line)
        return None
    try:
        json.loads(line)
    except ValueError:
        log.warning("garbled line from radio: %r", line[:80])
        return None
    return line.encode()


def main() -> None:
    import serial  # pyserial, only needed on the Pi
    from achal_common import load_site, topics
    from achal_common.mqtt import connect

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--gateway", default="127.0.0.1:1700", help="host:port of services/gateway UDP input")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    site = load_site()
    host, port = args.gateway.rsplit(":", 1)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ser = serial.Serial(args.port, args.baud, timeout=1)
    write_lock = threading.Lock()
    sirens = SirenState([s.id for s in site.sirens])

    def write(cmd: str) -> None:
        with write_lock:
            ser.write(cmd.encode())

    def on_message(topic: str, payload: bytes) -> None:
        siren_id = topic.split("/")[3]
        cmd = sirens.apply(siren_id, json.loads(payload))
        if cmd:
            write(cmd)

    client = connect("uart-bridge", on_message, [topics.siren_cmd(site.site_id)])
    client.loop_start()

    write(f"P {site.mesh.sample_period_s}\n")
    last_sync = 0.0
    while True:
        if time.time() - last_sync > 60:
            write(f"T {int(time.time())}\n")
            last_sync = time.time()
        raw = ser.readline().decode(errors="replace")
        datagram = route_line(raw)
        if datagram:
            udp.sendto(datagram, (host, int(port)))


if __name__ == "__main__":
    main()
