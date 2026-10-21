# SPDX-License-Identifier: MIT
"""Edge gateway (Raspberry Pi role): radio in, MQTT out, keeps working offline.

* Receives node frames over UDP (stand-in for the LoRa concentrator), validates
  them and stamps `rx_ts`.
* Drops duplicates: the mesh can deliver one reading twice (lost ACK, second
  path). Keyed on (site, node, seq, ts) and kept on disk across restarts.
* Store-and-forward: every message goes to a SQLite outbox first and leaves it
  only when the broker has acknowledged it (MQTT QoS 1 PUBACK), so an uplink
  cut or a broker restart loses nothing. Replay keeps arrival order, siren
  commands jump the queue.
* Local rules (rules.py) sound the on-site sirens with no cloud and no uplink;
  siren commands from the cloud risk engine are relayed to the sirens too.
* Health: /metrics (Prometheus text) and /status (JSON) on GATEWAY_HTTP_PORT,
  plus a retained status message on achal/{site}/gateway/status.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from achal_common import Telemetry, load_site, topics
from achal_common.config import SiteConfig
from rules import LocalRules, SirenDecision

log = logging.getLogger("gateway")

PRIORITY_SIREN = 10
PRIORITY_TELEMETRY = 0


class Buffer:
    """Store-and-forward queue. Survives restarts and uplink loss."""

    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")  # durable on power loss, far fewer fsyncs on an SD card
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, topic TEXT, payload TEXT)")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(outbox)")}
        if "priority" not in cols:  # buffers written by v0.1 have no priority column
            self.db.execute("ALTER TABLE outbox ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
        self.db.execute("CREATE INDEX IF NOT EXISTS outbox_order ON outbox (priority DESC, id)")
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (site TEXT, node TEXT, seq INTEGER, ts TEXT, "
                        "rx REAL, PRIMARY KEY (site, node, seq, ts))")
        self.db.commit()
        self.lock = threading.Lock()

    def put(self, topic: str, payload: str, priority: int = PRIORITY_TELEMETRY) -> None:
        with self.lock:
            self.db.execute("INSERT INTO outbox (topic, payload, priority) VALUES (?, ?, ?)",
                            (topic, payload, priority))
            self.db.commit()

    def peek(self, n: int = 200, exclude: set[int] | None = None) -> list[tuple[int, str, str]]:
        with self.lock:
            rows = self.db.execute("SELECT id, topic, payload FROM outbox ORDER BY priority DESC, id LIMIT ?",
                                   (n + len(exclude or ()),)).fetchall()
        return [r for r in rows if not exclude or r[0] not in exclude][:n]

    def drop(self, ids: list[int]) -> None:
        with self.lock:
            self.db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
            self.db.commit()

    def size(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def first_seen(self, site: str, node: str, seq: int, ts: str) -> bool:
        """Record a reading's identity; False if it was already received."""
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?, ?, ?, ?)",
                                  (site, node, seq, ts, time.time()))
            self.db.commit()
            return cur.rowcount == 1

    def prune_seen(self, older_than_s: float = 86_400) -> None:
        with self.lock:
            self.db.execute("DELETE FROM seen WHERE rx < ?", (time.time() - older_than_s,))
            self.db.commit()


class Metrics:
    COUNTERS = ("frames_rx", "frames_bad", "frames_duplicate", "readings_unique", "published", "replayed",
                "siren_commands", "uplink_drops")

    def __init__(self) -> None:
        self.started = time.time()
        self.c = dict.fromkeys(self.COUNTERS, 0)
        self.max_buffered = 0
        self.nodes: dict[str, dict] = {}  # last seen, hops, rssi per node
        self.lock = threading.Lock()

    def inc(self, name: str, n: int = 1) -> None:
        with self.lock:
            self.c[name] += n


class Gateway:
    def __init__(self, site: SiteConfig, buffer: Buffer, client, rules: LocalRules | None = None) -> None:
        self.site = site
        self.buf = buffer
        self.client = client
        self.rules = rules or LocalRules(site)
        self.metrics = Metrics()
        self.sirens: dict[str, dict] = {s.id: {"on": False} for s in site.sirens}
        self.inflight: dict[int, int] = {}  # MQTT message id -> outbox row id
        self.early_acks: set[int] = set()
        self.lock = threading.Lock()
        self.was_online = False

    # ---- radio side ----------------------------------------------------

    def handle_frame(self, data: bytes) -> Telemetry | None:
        self.metrics.inc("frames_rx")
        try:
            reading = Telemetry.model_validate_json(data)
        except ValueError as exc:
            self.metrics.inc("frames_bad")
            log.warning("dropping bad frame: %s", exc)
            return None
        if reading.site != self.site.site_id:
            self.metrics.inc("frames_bad")
            log.warning("dropping frame for site %s", reading.site)
            return None
        if not self.buf.first_seen(reading.site, reading.node, reading.seq, reading.ts.isoformat()):
            self.metrics.inc("frames_duplicate")
            return None
        self.metrics.inc("readings_unique")
        reading.rx_ts = datetime.now(UTC)
        reading.buffered = not self.online()
        with self.metrics.lock:
            self.metrics.nodes[reading.node] = {"last_ts": reading.ts.isoformat(), "hops": reading.hops,
                                                "rssi_dbm": reading.rssi_dbm, "batt_v": reading.batt_v,
                                                "rx": time.time()}
        self.buf.put(topics.telemetry(self.site.site_id, reading.node), reading.model_dump_json(exclude_none=True))
        for decision in self.rules.observe(reading):
            self.actuate(decision)
        return reading

    # ---- sirens --------------------------------------------------------

    def actuate(self, d: SirenDecision, source: str = "gateway") -> None:
        """Drive a siren over the local radio, then tell the cloud (now or when the uplink returns)."""
        state = {"on": d.on, "level": d.level, "ts": d.ts.isoformat(), "source": source,
                 "nodes": d.nodes, "reasons": d.reasons}
        self.sirens[d.siren_id] = state
        self.metrics.inc("siren_commands")
        log.warning("SIREN %s %s level=%s nodes=%s (%s, uplink %s)", d.siren_id, "ON" if d.on else "OFF",
                    d.level, ",".join(d.nodes), source, "up" if self.online() else "DOWN, local only")
        if source == "gateway":
            self.buf.put(topics.siren_cmd(self.site.site_id, d.siren_id), json.dumps(state), PRIORITY_SIREN)

    def on_cloud_siren_cmd(self, topic: str, payload: bytes) -> None:
        msg = json.loads(payload)
        if msg.get("source") == "gateway":
            return  # our own command coming back from the broker
        siren_id = topic.split("/")[3]
        if siren_id not in self.sirens:
            return
        self.sirens[siren_id] = {**msg, "source": msg.get("source", "cloud")}
        self.metrics.inc("siren_commands")
        log.warning("SIREN %s %s from cloud", siren_id, "ON" if msg.get("on") else "OFF")

    # ---- uplink side ---------------------------------------------------

    def online(self) -> bool:
        return bool(self.client.is_connected())

    def on_publish(self, mid: int) -> None:
        """PUBACK from the broker: only now is the row safe to delete."""
        with self.lock:
            row = self.inflight.pop(mid, None)
            if row is None:
                self.early_acks.add(mid)  # acknowledged before flush_once recorded the mid
                return
        self.buf.drop([row])
        self.metrics.inc("published")

    def on_disconnect(self) -> None:
        with self.lock:
            lost = len(self.inflight)
            self.inflight.clear()
            self.early_acks.clear()  # unacknowledged rows stay in the outbox and are sent again
        if lost:
            self.metrics.inc("uplink_drops", lost)

    def flush_once(self, batch: int = 500) -> int:
        if not self.online():
            self.was_online = False
            return 0
        replay = not self.was_online
        self.was_online = True
        with self.lock:
            pending = set(self.inflight.values())
        sent = 0
        for row_id, topic, payload in self.buf.peek(batch, exclude=pending):
            # Never hold self.lock across publish(): paho calls on_publish while holding its own locks.
            info = self.client.publish(topic, payload, qos=1)
            if info.rc != 0:
                break
            with self.lock:
                acked = info.mid in self.early_acks
                self.early_acks.discard(info.mid)
                if not acked:
                    self.inflight[info.mid] = row_id
            if acked:
                self.buf.drop([row_id])
                self.metrics.inc("published")
            sent += 1
            if '"buffered":true' in payload:
                self.metrics.inc("replayed")
        if replay and sent:
            log.info("uplink up: draining %d buffered messages", self.buf.size())
        return sent

    def status(self) -> dict:
        size = self.buf.size()
        self.metrics.max_buffered = max(self.metrics.max_buffered, size)
        period = self.site.mesh.sample_period_s
        now = time.time()
        with self.metrics.lock:
            heard = dict(self.metrics.nodes)
            counters = dict(self.metrics.c)
        speed = float(os.environ.get("SIM_SPEED", "1"))
        silent = sorted(n for n in self.site.node_ids()
                        if n not in heard or now - heard[n]["rx"] > 5 * period / max(speed, 1e-9))
        return {
            "online": self.online(), "buffered": size, "ts": datetime.now(UTC).isoformat(),
            "gateway": self.site.gateway.id, "uptime_s": round(now - self.metrics.started),
            "max_buffered": self.metrics.max_buffered, "inflight": len(self.inflight), **counters,
            "nodes_heard": len(heard), "silent_nodes": silent,
            "sirens": {k: {"on": v.get("on", False), "level": v.get("level"), "source": v.get("source")}
                       for k, v in self.sirens.items()},
        }

    def metrics_text(self) -> str:
        s = self.status()
        gw = f'gateway="{s["gateway"]}",site="{self.site.site_id}"'
        lines = []
        for name in Metrics.COUNTERS:
            lines += [f"# TYPE achal_gateway_{name}_total counter", f"achal_gateway_{name}_total{{{gw}}} {s[name]}"]
        for name in ("buffered", "max_buffered", "inflight", "nodes_heard", "uptime_s"):
            lines += [f"# TYPE achal_gateway_{name} gauge", f"achal_gateway_{name}{{{gw}}} {s[name]}"]
        lines += ["# TYPE achal_gateway_uplink_online gauge",
                  f"achal_gateway_uplink_online{{{gw}}} {int(s['online'])}",
                  "# TYPE achal_gateway_silent_nodes gauge",
                  f"achal_gateway_silent_nodes{{{gw}}} {len(s['silent_nodes'])}",
                  "# TYPE achal_gateway_siren_on gauge"]
        lines += [f'achal_gateway_siren_on{{{gw},siren="{k}"}} {int(v["on"])}' for k, v in s["sirens"].items()]
        with self.metrics.lock:
            nodes = dict(self.metrics.nodes)
        lines += ["# TYPE achal_node_rssi_dbm gauge"]
        lines += [f'achal_node_rssi_dbm{{{gw},node="{n}"}} {v["rssi_dbm"]}' for n, v in sorted(nodes.items())
                  if v.get("rssi_dbm") is not None]
        lines += ["# TYPE achal_node_hops gauge"]
        lines += [f'achal_node_hops{{{gw},node="{n}"}} {v["hops"]}' for n, v in sorted(nodes.items())
                  if v.get("hops") is not None]
        return "\n".join(lines) + "\n"


def mqtt_client(client_id: str, gw_ref: list):
    """Non-blocking client: the gateway must start and buffer even when the broker is down."""
    import paho.mqtt.client as mqtt

    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    client.max_inflight_messages_set(200)
    client.reconnect_delay_set(1, 15)

    def on_connect(c, _u, _f, reason_code, _p):
        log.info("uplink connected to mqtt %s:%s (%s)", host, port, reason_code)
        site_id = gw_ref[0].site.site_id
        c.subscribe(topics.siren_cmd(site_id), qos=1)

    def on_disconnect(_c, _u, _f, reason_code, _p):
        log.warning("uplink lost (%s): buffering locally", reason_code)
        gw_ref[0].on_disconnect()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = lambda _c, _u, mid, _rc, _p: gw_ref[0].on_publish(mid)
    client.on_message = lambda _c, _u, msg: gw_ref[0].on_cloud_siren_cmd(msg.topic, msg.payload)
    client.connect_async(host, port, keepalive=15)
    client.loop_start()
    return client


def serve_http(gw: Gateway, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            if self.path.startswith("/metrics"):
                body, ctype = gw.metrics_text().encode(), "text/plain; version=0.0.4"
            elif self.path.startswith("/status") or self.path == "/":
                body, ctype = json.dumps(gw.status()).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("health on http://0.0.0.0:%d/status and /metrics", port)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    site = load_site()
    buf = Buffer(os.environ.get("BUFFER_PATH", "/data/gateway-buffer.sqlite"))
    gw_ref: list[Gateway] = []
    client = mqtt_client(f"gateway-{site.gateway.id}", gw_ref)
    gw = Gateway(site, buf, client)
    gw_ref.append(gw)
    serve_http(gw, int(os.environ.get("GATEWAY_HTTP_PORT", "9100")))

    def flush_forever() -> None:
        last_status = last_prune = 0.0
        while True:
            sent = gw.flush_once()
            now = time.time()
            if now - last_status > 5 and gw.online():
                client.publish(topics.gateway_status(site.site_id), json.dumps(gw.status()), qos=0, retain=True)
                last_status = now
            if now - last_prune > 3600:
                buf.prune_seen()
                last_prune = now
            time.sleep(0.05 if sent else 0.5)

    threading.Thread(target=flush_forever, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", int(os.environ.get("GATEWAY_UDP_PORT", "1700"))))
    log.info("listening for node frames on udp/%s", sock.getsockname()[1])
    while True:
        data, _ = sock.recvfrom(4096)
        gw.handle_frame(data)


if __name__ == "__main__":
    main()
