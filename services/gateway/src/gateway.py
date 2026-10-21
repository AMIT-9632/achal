# SPDX-License-Identifier: MIT
"""Edge gateway (Raspberry Pi role): radio in, MQTT out, works offline.

SCAFFOLD STUB (W0). Receives node frames over UDP, stamps rx_ts, keeps every
reading in a SQLite buffer until MQTT accepts it, and publishes gateway status.

W2 adds: local rule check that drives sirens with no uplink, buffer replay
ordering, duplicate suppression across mesh paths, health metrics.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime

from achal_common import Telemetry, load_site, topics
from achal_common.mqtt import connect

log = logging.getLogger("gateway")


class Buffer:
    """Store-and-forward queue. Survives restarts and uplink loss."""

    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, topic TEXT, payload TEXT)")
        self.lock = threading.Lock()

    def put(self, topic: str, payload: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO outbox (topic, payload) VALUES (?, ?)", (topic, payload))
            self.db.commit()

    def peek(self, n: int = 200) -> list[tuple[int, str, str]]:
        with self.lock:
            return self.db.execute("SELECT id, topic, payload FROM outbox ORDER BY id LIMIT ?", (n,)).fetchall()

    def drop(self, ids: list[int]) -> None:
        with self.lock:
            self.db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
            self.db.commit()

    def size(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    site = load_site()
    buf = Buffer(os.environ.get("BUFFER_PATH", "/data/gateway-buffer.sqlite"))
    client = connect(f"gateway-{site.gateway.id}")
    client.loop_start()

    def flush_forever() -> None:
        while True:
            rows = buf.peek()
            sent = []
            if client.is_connected():
                for row_id, topic, payload in rows:
                    if client.publish(topic, payload, qos=1).rc == 0:
                        sent.append(row_id)
                buf.drop(sent)
            client.publish(topics.gateway_status(site.site_id), json.dumps({
                "online": client.is_connected(), "buffered": buf.size(),
                "ts": datetime.now(UTC).isoformat()}), retain=True)
            time.sleep(0.5 if sent else 2)

    threading.Thread(target=flush_forever, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", int(os.environ.get("GATEWAY_UDP_PORT", "1700"))))
    log.info("listening for node frames on udp/%s", sock.getsockname()[1])
    while True:
        data, _ = sock.recvfrom(4096)
        try:
            reading = Telemetry.model_validate_json(data)
        except ValueError as exc:
            log.warning("dropping bad frame: %s", exc)
            continue
        reading.rx_ts = datetime.now(UTC)
        reading.buffered = not client.is_connected()
        buf.put(topics.telemetry(site.site_id, reading.node), reading.model_dump_json(exclude_none=True))


if __name__ == "__main__":
    main()
