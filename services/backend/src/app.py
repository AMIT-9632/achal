# SPDX-License-Identifier: MIT
"""Backend API: REST + WebSocket for the dashboard.

SCAFFOLD STUB (W0). Mirrors the latest telemetry, risk and alerts from MQTT in
memory and streams them to WebSocket clients.

W2 adds: persistence to TimescaleDB + PostGIS (schema in infra/db/init.sql),
history queries, multi-site routing, auth for operator actions, cloud sync.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from contextlib import asynccontextmanager

from achal_common import load_site, topics
from achal_common.mqtt import connect
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("backend")
site = load_site()
latest: dict[str, dict] = {"telemetry": {}, "risk": {}}
alerts: deque[dict] = deque(maxlen=200)
clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None


def on_message(topic: str, payload: bytes) -> None:
    msg = json.loads(payload)
    kind = topic.split("/")[2]
    if kind in ("telemetry", "risk") and "node" in msg:
        latest[kind][msg["node"]] = msg
    elif kind == "alert":
        alerts.appendleft(msg)
    if loop:
        asyncio.run_coroutine_threadsafe(broadcast({"kind": kind, "data": msg}), loop)


async def broadcast(event: dict) -> None:
    dead = []
    for ws in clients:
        try:
            await ws.send_json(event)
        except Exception:  # noqa: BLE001 - a closed socket should not stop the fan-out
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global loop
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    loop = asyncio.get_running_loop()
    client = connect("backend", on_message, [topics.telemetry(site.site_id), topics.risk(site.site_id),
                                              topics.alert(site.site_id)])
    client.loop_start()
    yield
    client.loop_stop()


app = FastAPI(title="ACHAL API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health() -> dict:
    return {"ok": True, "site": site.site_id}


@app.get("/api/site")
def get_site() -> dict:
    nodes = []
    for n in site.node_ids():
        p = site.node_position_m(n)
        lat, lon = site.to_latlon(p)
        nodes.append({"id": n, "x_m": p.x, "y_m": p.y, "lat": lat, "lon": lon})
    sirens = [{"id": s.id, "label": s.label, **dict(zip(("lat", "lon"), site.to_latlon(s.position_m), strict=True))}
              for s in site.sirens]
    return {"site": site.model_dump(), "nodes": nodes, "sirens": sirens}


@app.get("/api/nodes")
def get_nodes() -> dict:
    return latest


@app.get("/api/alerts")
def get_alerts() -> list[dict]:
    return list(alerts)


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
