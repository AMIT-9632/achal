# SPDX-License-Identifier: MIT
"""Backend API: REST + WebSocket for the dashboard, history in TimescaleDB + PostGIS.

* Subscribes to every site's topics (`achal/+/...`), so one backend serves many
  coalfields; each site is a YAML file in config/sites/.
* Keeps the latest state per site in memory for the live map and streams every
  message to WebSocket clients as {"kind", "site", "data"}.
* Persists telemetry, risk, alerts, siren events, gateway status and alert
  deliveries through store.py (TimescaleDB when DATABASE_URL is set, else RAM).
* Operator actions (alert acknowledge) need `Authorization: Bearer $OPERATOR_TOKEN`
  when OPERATOR_TOKEN is set.

All query endpoints take `?site=` and default to SITE_ID.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from achal_common import Alert, RiskAssessment, SiteConfig, Telemetry, load_site, topics
from achal_common.config import config_dir
from fastapi import Body, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from store import MemoryStore, PgStore, parse_ts, window

log = logging.getLogger("backend")

DEFAULT_SITE = os.environ.get("SITE_ID", "jharia-demo")
SUBSCRIPTIONS = [f"{topics.ROOT}/+/telemetry/+", f"{topics.ROOT}/+/risk/+", f"{topics.ROOT}/+/alert",
                 f"{topics.ROOT}/+/alert/+", f"{topics.ROOT}/+/gateway/status", f"{topics.ROOT}/+/siren/+/cmd"]
Minutes = Annotated[float | None, Query(gt=0, description="Window back from the newest reading (simulated time)")]
From = Annotated[datetime | None, Query(alias="from")]
Limit = Annotated[int, Query(ge=1, le=20_000)]
VALIDATORS = {"telemetry": Telemetry, "risk": RiskAssessment, "alert": Alert}


def load_sites() -> dict[str, SiteConfig]:
    ids = sorted(p.stem for p in (config_dir() / "sites").glob("*.yaml"))
    return {i: load_site(i) for i in ids}


def classify(topic: str) -> tuple[str, str, str | None] | None:
    """Topic -> (site, kind, id). See docs/mqtt-topics.md."""
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != topics.ROOT:
        return None
    site, rest = parts[1], parts[2:]
    match rest:
        case ["telemetry", node]:
            return site, "telemetry", node
        case ["risk", "grid"]:
            return site, "grid", None
        case ["risk", node]:
            return site, "risk", node
        case ["alert"]:
            return site, "alert", None
        case ["alert", "ack"]:
            return site, "alert_ack", None
        case ["alert", "delivery"]:
            return site, "delivery", None
        case ["gateway", "status"]:
            return site, "gateway", None
        case ["siren", siren_id, "cmd"]:
            return site, "siren", siren_id
    return None


class SiteState:
    def __init__(self) -> None:
        self.telemetry: dict[str, dict] = {}
        self.risk: dict[str, dict] = {}
        self.gateway: dict | None = None
        self.grid: dict | None = None
        self.sirens: dict[str, dict] = {}
        self.deliveries: deque[dict] = deque(maxlen=500)


class Hub:
    """Everything the API shares: sites, store, live state, sockets, MQTT."""

    def __init__(self, store, sites: dict[str, SiteConfig]) -> None:
        self.store = store
        self.sites = sites
        self.state = {s: SiteState() for s in sites}
        self.clients: dict[WebSocket, str] = {}  # socket -> site filter ("*" = all)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue | None = None
        self.mqtt = None
        self.pending: dict[str, list[dict]] = {"telemetry": [], "risk": []}
        self.counters = {"messages": 0, "invalid": 0, "unknown_site": 0, "write_errors": 0}
        self._warned: set[str] = set()

    # ---- ingest --------------------------------------------------------

    def submit(self, topic: str, payload: bytes) -> None:
        """Thread-safe entry point for the MQTT callback thread."""
        if self.loop and self.queue:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (topic, payload))

    async def consume(self) -> None:
        while True:
            topic, payload = await self.queue.get()
            try:
                await self.ingest(topic, payload)
            except Exception:  # noqa: BLE001 - one bad message must not stop ingest
                log.exception("failed to ingest %s", topic)

    async def ingest(self, topic: str, payload: bytes | str | dict) -> None:
        c = classify(topic)
        if c is None:
            return
        site, kind, ident = c
        if site not in self.sites:
            self.counters["unknown_site"] += 1
            return
        msg = payload if isinstance(payload, dict) else json.loads(payload)
        self.counters["messages"] += 1
        if kind in VALIDATORS:
            try:
                msg = VALIDATORS[kind].model_validate(msg).model_dump(mode="json", exclude_none=True)
            except ValidationError as exc:
                self.counters["invalid"] += 1
                if kind not in self._warned:  # keep going with the raw message, but say so once
                    log.warning("%s message does not match schema v1, storing raw: %s", kind, exc.errors()[:2])
                    self._warned.add(kind)
                if kind == "telemetry" and not {"node", "seq", "ts"} <= msg.keys():
                    return
        st = self.state[site]
        if kind in ("telemetry", "risk", "alert"):
            msg.setdefault("site", site)
        match kind:
            case "telemetry":
                st.telemetry[msg["node"]] = msg
                self.pending["telemetry"].append(msg)
            case "risk":
                st.risk[msg["node"]] = msg
                self.pending["risk"].append(msg)
            case "grid":
                st.grid = msg
            case "alert":
                await self._write(self.store.add_alert(msg))
            case "alert_ack":
                alert, changed = await self.store.ack_alert(msg["id"], msg.get("by", "unknown"),
                                                            parse_ts(msg.get("ts")) or datetime.now(UTC))
                if not changed:
                    return  # unknown alert, or our own acknowledgement echoed back by the broker
                msg = self._ack_event(alert)
            case "delivery":
                st.deliveries.appendleft(msg)
                await self._write(self.store.add_event("delivery", site, msg))
            case "gateway":
                st.gateway = msg
                await self._write(self.store.add_event("gateway", site, msg))
            case "siren":
                msg = {**msg, "id": ident}
                st.sirens[ident] = msg
                await self._write(self.store.add_event("siren", site, msg))
        await self.broadcast({"kind": kind, "site": site, "data": msg})

    async def _write(self, coro) -> bool:
        try:
            await coro
            return True
        except Exception:  # noqa: BLE001 - keep serving live data if the database hiccups
            self.counters["write_errors"] += 1
            log.exception("store write failed")
            return False

    async def flush_writes(self) -> None:
        for kind, fn in (("telemetry", self.store.add_telemetry), ("risk", self.store.add_risk)):
            batch, self.pending[kind] = self.pending[kind], []
            if batch and not await self._write(fn(batch)):
                self.pending[kind] = (batch + self.pending[kind])[-100_000:]  # retry next tick

    async def writer(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            await self.flush_writes()

    @staticmethod
    def _ack_event(alert: dict) -> dict:
        return {"id": alert["id"], "by": alert["acked_by"], "ts": alert["acked_ts"],
                "acked_by": alert["acked_by"], "acked_ts": alert["acked_ts"]}

    # ---- fan-out -------------------------------------------------------

    async def broadcast(self, event: dict) -> None:
        dead = []
        for ws, flt in list(self.clients.items()):
            if flt not in ("*", event["site"]):
                continue
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 - a closed socket should not stop the fan-out
                dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)

    # ---- MQTT ----------------------------------------------------------

    def start_mqtt(self) -> None:
        from achal_common.mqtt import connect

        def run() -> None:
            client = connect("backend", lambda t, p: self.submit(t, p), SUBSCRIPTIONS)
            self.mqtt = client
            client.loop_start()

        threading.Thread(target=run, daemon=True, name="mqtt-connect").start()

    def publish(self, topic: str, msg: dict) -> bool:
        if self.mqtt is None or not self.mqtt.is_connected():
            return False
        return self.mqtt.publish(topic, json.dumps(msg), qos=1).rc == 0


def make_store():
    dsn = os.environ.get("DATABASE_URL")
    return PgStore(dsn) if dsn else MemoryStore()


def create_app(store=None, sites: dict[str, SiteConfig] | None = None, enable_mqtt: bool | None = None) -> FastAPI:
    sites = sites or load_sites()
    hub = Hub(store or make_store(), sites)
    if enable_mqtt is None:
        enable_mqtt = os.environ.get("BACKEND_MQTT", "1") != "0"
    token = os.environ.get("OPERATOR_TOKEN", "")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        hub.loop = asyncio.get_running_loop()
        hub.queue = asyncio.Queue()
        await hub.store.start(sites)
        tasks = [asyncio.create_task(hub.consume()), asyncio.create_task(hub.writer())]
        if enable_mqtt:
            hub.start_mqtt()
        log.info("serving sites %s, store=%s", ", ".join(sites), hub.store.kind)
        yield
        for t in tasks:
            t.cancel()
        await hub.flush_writes()
        if hub.mqtt:
            hub.mqtt.loop_stop()
        await hub.store.close()

    app = FastAPI(title="ACHAL API", version="0.2.0", lifespan=lifespan,
                  description="Mine subsidence early warning: live state, history and operator actions. "
                              "All data in this build is synthetic.")
    app.state.hub = hub
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def site_of(site: str | None) -> SiteConfig:
        s = site or DEFAULT_SITE
        if s not in sites:
            raise HTTPException(404, f"unknown site {s!r}")
        return sites[s]

    def node_of(cfg: SiteConfig, node: str) -> str:
        if node not in cfg.node_ids():
            raise HTTPException(404, f"unknown node {node!r} at {cfg.site_id}")
        return node

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "site": DEFAULT_SITE, "sites": list(sites), "store": hub.store.kind,
                "mqtt": bool(hub.mqtt and hub.mqtt.is_connected())}

    @app.get("/api/sites")
    def list_sites() -> list[dict]:
        return [{"site_id": s.site_id, "name": s.name, "district": s.district, "state": s.state,
                 "languages": s.languages, "nodes": len(s.node_ids()), "gateway": s.gateway.id,
                 "lat": s.origin.lat, "lon": s.origin.lon, "people": s.exposure.people} for s in sites.values()]

    @app.get("/api/site")
    def get_site(site: str | None = None) -> dict:
        cfg = site_of(site)
        nodes = []
        for n in cfg.node_ids():
            p = cfg.node_position_m(n)
            lat, lon = cfg.to_latlon(p)
            nodes.append({"id": n, "x_m": p.x, "y_m": p.y, "lat": lat, "lon": lon})
        sirens = [{"id": s.id, "label": s.label, **dict(zip(("lat", "lon"), cfg.to_latlon(s.position_m), strict=True))}
                  for s in cfg.sirens]
        gw_lat, gw_lon = cfg.to_latlon(cfg.gateway.position_m)
        return {"site": cfg.model_dump(), "nodes": nodes, "sirens": sirens,
                "gateway": {"id": cfg.gateway.id, "lat": gw_lat, "lon": gw_lon}}

    @app.get("/api/nodes")
    def get_nodes(site: str | None = None) -> dict:
        st = hub.state[site_of(site).site_id]
        return {"telemetry": st.telemetry, "risk": st.risk, "gateway": st.gateway, "grid": st.grid,
                "sirens": st.sirens}

    @app.get("/api/nodes/geojson")
    def nodes_geojson(site: str | None = None) -> dict:
        """Latest state per node as GeoJSON points, for GIS tools (QGIS, Leaflet)."""
        cfg = site_of(site)
        st = hub.state[cfg.site_id]
        feats = []
        for n in cfg.node_ids():
            lat, lon = cfg.to_latlon(cfg.node_position_m(n))
            t, r = st.telemetry.get(n, {}), st.risk.get(n, {})
            props = {"id": n, "level": r.get("level"), "score": r.get("score"), "ttf_hours": r.get("ttf_hours"),
                     "reasons": r.get("reasons"), "ts": t.get("ts"), "tilt_x_deg": t.get("tilt_x_deg"),
                     "tilt_y_deg": t.get("tilt_y_deg"), "crack_mm": t.get("crack_mm"), "batt_v": t.get("batt_v"),
                     "hops": t.get("hops"), "rssi_dbm": t.get("rssi_dbm"), "synthetic": True}
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": props})
        return {"type": "FeatureCollection", "features": feats}

    @app.get("/api/nodes/near")
    async def nodes_near(lat: float, lon: float, radius_m: Annotated[float, Query(gt=0, le=10_000)] = 100,
                         site: str | None = None) -> list[dict]:
        """Nodes within radius_m of a point (PostGIS ST_DWithin with the database)."""
        return await hub.store.nodes_near(site_of(site).site_id, lat, lon, radius_m)

    @app.get("/api/nodes/{node}/history")
    async def node_history(node: str, site: str | None = None, minutes: Minutes = None, from_: From = None,
                           to: datetime | None = None, limit: Limit = 2000,
                           bucket_s: Annotated[int | None, Query(ge=1, description="Average per bucket")] = None,
                           ) -> list[dict]:
        """Telemetry for one node, oldest first, with the risk score/level in force at each reading."""
        cfg = site_of(site)
        node_of(cfg, node)
        await hub.flush_writes()
        latest = await hub.store.latest_ts(cfg.site_id, node) if minutes else None
        since, until = window(latest, minutes, from_, to)
        return await hub.store.history(cfg.site_id, node, since, until, limit, bucket_s)

    @app.get("/api/nodes/{node}/risk")
    async def node_risk(node: str, site: str | None = None, minutes: Minutes = None, from_: From = None,
                        to: datetime | None = None, limit: Limit = 2000) -> list[dict]:
        cfg = site_of(site)
        node_of(cfg, node)
        await hub.flush_writes()
        latest = await hub.store.latest_ts(cfg.site_id, node) if minutes else None
        since, until = window(latest, minutes, from_, to)
        return await hub.store.risk_history(cfg.site_id, node, since, until, limit)

    @app.get("/api/alerts")
    async def get_alerts(site: str | None = None, limit: Annotated[int, Query(ge=1, le=2000)] = 200,
                         unacked: bool = False) -> list[dict]:
        return await hub.store.list_alerts(site_of(site).site_id, limit, unacked)

    @app.post("/api/alerts/{alert_id}/ack")
    async def ack_alert(alert_id: str, body: Annotated[dict[str, Any] | None, Body()] = None,
                        authorization: Annotated[str | None, Header()] = None) -> dict:
        body = body or {}
        if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
            raise HTTPException(401, "operator token required", headers={"WWW-Authenticate": "Bearer"})
        by = str(body.get("by") or "operator")[:80]
        alert, changed = await hub.store.ack_alert(alert_id, by, datetime.now(UTC))
        if alert is None:
            raise HTTPException(404, "unknown alert")
        event = Hub._ack_event(alert)
        if changed:
            hub.publish(f"{topics.ROOT}/{alert['site']}/alert/ack", {"id": alert_id, "by": event["by"],
                                                                     "ts": event["ts"]})
            await hub.broadcast({"kind": "alert_ack", "site": alert["site"], "data": event})
        return alert

    @app.get("/api/gateway")
    def gateway(site: str | None = None) -> dict:
        st = hub.state[site_of(site).site_id]
        return st.gateway or {"online": False, "buffered": None, "ts": None}

    @app.get("/api/deliveries")
    def deliveries(site: str | None = None) -> list[dict]:
        return list(hub.state[site_of(site).site_id].deliveries)

    @app.get("/api/stats")
    async def stats(site: str | None = None) -> dict:
        """Row counts, for checking that an uplink outage lost nothing."""
        cfg = site_of(site)
        await hub.flush_writes()
        return {**await hub.store.stats(cfg.site_id), "ingest": hub.counters}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, site: str | None = None) -> None:
        await websocket.accept()
        hub.clients[websocket] = site or DEFAULT_SITE
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hub.clients.pop(websocket, None)

    return app


app = create_app()
