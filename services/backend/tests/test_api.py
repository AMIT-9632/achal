# SPDX-License-Identifier: MIT
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from app import classify, create_app
from fastapi.testclient import TestClient
from store import MemoryStore, PgStore

# Set ACHAL_TEST_DATABASE_URL (e.g. postgresql://achal:change-me@localhost:5432/achal) to run every API
# test against TimescaleDB + PostGIS as well as the in-memory store. CI does this in the db job.
PG_URL = os.environ.get("ACHAL_TEST_DATABASE_URL")
TABLES = "telemetry, risk, alerts, siren_events, gateway_status, alert_deliveries"


def make_store(kind: str):
    if kind == "memory":
        return MemoryStore()
    if not PG_URL:
        pytest.skip("ACHAL_TEST_DATABASE_URL not set")
    return PgStore(PG_URL)


def truncate(client, store) -> None:
    async def run():
        async with store.pool.acquire() as c:
            await c.execute(f"TRUNCATE {TABLES}")

    if isinstance(store, PgStore):
        client.portal.call(run)

T0 = datetime(2026, 10, 21, tzinfo=UTC)


def telemetry(node: str, seq: int, site: str = "jharia-demo", **over) -> dict:
    return {"v": 1, "site": site, "node": node, "seq": seq, "ts": (T0 + timedelta(minutes=seq)).isoformat(),
            "tilt_x_deg": 0.001 * seq, "tilt_y_deg": 0.0, "vib_rms_g": 0.0005, "crack_mm": 0.1, "ext_mm": 0.0,
            "batt_v": 3.9, "hops": 2, "path": [node, "n07"], "rssi_dbm": -97.5, **over}


def risk(node: str, seq: int, score: float, level: str, site: str = "jharia-demo") -> dict:
    return {"v": 1, "site": site, "node": node, "ts": (T0 + timedelta(minutes=seq)).isoformat(), "score": score,
            "level": level, "reasons": ["tilt_rate_high"], "components": {"rules": score}}


def alert(alert_id: str, site: str = "jharia-demo") -> dict:
    return {"v": 1, "id": alert_id, "site": site, "ts": T0.isoformat(), "level": "warning",
            "previous_level": "watch", "nodes": ["n12", "n13"], "reasons": ["neighbour_consensus"], "siren": True}


@pytest.fixture(params=["memory", "pg"])
def api(request, monkeypatch):
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
    store = make_store(request.param)
    app = create_app(store=store, enable_mqtt=False)
    with TestClient(app) as client:
        truncate(client, store)
        hub = app.state.hub

        def push(topic: str, msg: dict) -> None:
            client.portal.call(hub.ingest, topic, json.dumps(msg).encode())

        client.push = push
        client.hub = hub
        yield client


def test_topic_routing():
    assert classify("achal/jharia-demo/telemetry/n01") == ("jharia-demo", "telemetry", "n01")
    assert classify("achal/jharia-demo/risk/grid") == ("jharia-demo", "grid", None)
    assert classify("achal/jharia-demo/risk/n02") == ("jharia-demo", "risk", "n02")
    assert classify("achal/x/alert/ack") == ("x", "alert_ack", None)
    assert classify("achal/x/alert/delivery") == ("x", "delivery", None)
    assert classify("achal/x/siren/siren-01/cmd") == ("x", "siren", "siren-01")
    assert classify("achal/x/gateway/status") == ("x", "gateway", None)
    assert classify("other/x/telemetry/n01") is None


def test_sites_listing_is_multi_site(api):
    ids = {s["site_id"] for s in api.get("/api/sites").json()}
    assert {"jharia-demo", "jharia-colliery"} <= ids
    assert len(api.get("/api/site", params={"site": "jharia-colliery"}).json()["nodes"]) == 100
    assert api.get("/api/site", params={"site": "nowhere"}).status_code == 404


def test_latest_state_is_per_site(api):
    api.push("achal/jharia-demo/telemetry/n01", telemetry("n01", 1))
    api.push("achal/jharia-colliery/telemetry/n55", telemetry("n55", 1, site="jharia-colliery"))
    api.push("achal/jharia-demo/gateway/status", {"online": True, "buffered": 0, "ts": T0.isoformat()})
    api.push("achal/jharia-demo/risk/grid", {"ts": T0.isoformat(), "cells": []})
    api.push("achal/jharia-demo/siren/siren-01/cmd", {"on": True, "level": "critical", "ts": T0.isoformat()})
    demo = api.get("/api/nodes").json()
    assert set(demo) == {"telemetry", "risk", "gateway", "grid", "sirens"}
    assert list(demo["telemetry"]) == ["n01"]
    assert demo["gateway"]["online"] is True and demo["grid"] == {"ts": T0.isoformat(), "cells": []}
    assert demo["sirens"]["siren-01"]["id"] == "siren-01"
    assert list(api.get("/api/nodes", params={"site": "jharia-colliery"}).json()["telemetry"]) == ["n55"]


def test_history_is_oldest_first_with_risk_merged(api):
    for k in range(10):
        api.push("achal/jharia-demo/telemetry/n03", telemetry("n03", k))
    api.push("achal/jharia-demo/risk/n03", risk("n03", 4, 0.4, "watch"))
    api.push("achal/jharia-demo/risk/n03", risk("n03", 7, 0.7, "warning"))
    rows = api.get("/api/nodes/n03/history").json()
    assert [r["seq"] for r in rows] == list(range(10))
    assert "score" not in rows[3]
    assert (rows[4]["level"], rows[6]["level"], rows[9]["score"]) == ("watch", "watch", 0.7)
    # minutes counts back from the newest reading (simulated time runs ahead of the wall clock)
    assert [r["seq"] for r in api.get("/api/nodes/n03/history", params={"minutes": 3}).json()] == [6, 7, 8, 9]
    assert len(api.get("/api/nodes/n03/risk").json()) == 2
    assert api.get("/api/nodes/n99/history").status_code == 404


def test_history_buckets(api):
    for k in range(10):
        api.push("achal/jharia-demo/telemetry/n03", telemetry("n03", k))
    rows = api.get("/api/nodes/n03/history", params={"bucket_s": 300}).json()
    assert [r["n"] for r in rows] == [5, 5]
    assert rows[0]["tilt_x_deg"] == pytest.approx(0.002)


def test_replayed_readings_are_stored_once(api):
    for _ in range(3):
        api.push("achal/jharia-demo/telemetry/n05", telemetry("n05", 1, buffered=True))
    stats = api.get("/api/stats").json()
    assert stats["telemetry_rows"] == 1 and stats["buffered_rows"] == 1


def test_alert_ack_flow(api, monkeypatch):
    api.push("achal/jharia-demo/alert", alert("a-1"))
    published = []
    monkeypatch.setattr(api.hub, "publish", lambda topic, msg: published.append((topic, msg)) or True)
    with api.websocket_connect("/ws") as ws:
        r = api.post("/api/alerts/a-1/ack", json={"by": "Shift officer"})
        assert r.status_code == 200 and r.json()["acked_by"] == "Shift officer"
        event = ws.receive_json()
        assert event["kind"] == "alert_ack" and event["data"]["id"] == "a-1"
    assert published == [("achal/jharia-demo/alert/ack", {"id": "a-1", "by": "Shift officer",
                                                         "ts": r.json()["acked_ts"]})]
    # second ack keeps the first, and the broker echo of our own ack is ignored
    assert api.post("/api/alerts/a-1/ack", json={"by": "someone else"}).json()["acked_by"] == "Shift officer"
    api.push("achal/jharia-demo/alert/ack", {"id": "a-1", "by": "echo", "ts": T0.isoformat()})
    alerts = api.get("/api/alerts").json()
    assert alerts[0]["acked_by"] == "Shift officer" and alerts[0]["acked_ts"]
    assert api.get("/api/alerts", params={"unacked": True}).json() == []
    assert api.post("/api/alerts/nope/ack", json={}).status_code == 404


def test_ack_from_alerts_service_is_applied(api):
    api.push("achal/jharia-demo/alert", alert("a-2"))
    api.push("achal/jharia-demo/alert/ack", {"id": "a-2", "by": "sms:+91XXXXXXXX", "ts": T0.isoformat()})
    assert api.get("/api/alerts").json()[0]["acked_by"] == "sms:+91XXXXXXXX"


def test_ack_needs_token_when_configured(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", "s3cret")
    app = create_app(store=MemoryStore(), enable_mqtt=False)
    with TestClient(app) as client:
        client.portal.call(app.state.hub.ingest, "achal/jharia-demo/alert", json.dumps(alert("a-3")).encode())
        assert client.post("/api/alerts/a-3/ack", json={"by": "x"}).status_code == 401
        ok = client.post("/api/alerts/a-3/ack", json={"by": "x"}, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_websocket_filters_by_site_and_labels_kinds(api):
    with api.websocket_connect("/ws") as ws, api.websocket_connect("/ws?site=*") as all_ws:
        api.push("achal/jharia-colliery/telemetry/n10", telemetry("n10", 1, site="jharia-colliery"))
        api.push("achal/jharia-demo/alert/delivery", {"alert_id": "a-1", "recipient": "x", "channel": "sms",
                                                      "lang": "hi", "status": "sent", "ts": T0.isoformat()})
        assert all_ws.receive_json()["site"] == "jharia-colliery"
        assert all_ws.receive_json()["kind"] == "delivery"
        assert ws.receive_json()["kind"] == "delivery"  # the colliery reading was not sent to this socket


def test_geojson_and_near(api):
    api.push("achal/jharia-demo/telemetry/n12", telemetry("n12", 1))
    api.push("achal/jharia-demo/risk/n12", risk("n12", 1, 0.9, "critical"))
    fc = api.get("/api/nodes/geojson").json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 25
    centre = next(f for f in fc["features"] if f["properties"]["id"] == "n12")
    assert centre["properties"]["level"] == "critical"
    lon, lat = centre["geometry"]["coordinates"]
    near = api.get("/api/nodes/near", params={"lat": lat, "lon": lon, "radius_m": 25}).json()
    assert near[0] == {"id": "n12", "distance_m": 0.0}
    assert {n["id"] for n in near} == {"n12", "n07", "n11", "n13", "n17"}


def test_unknown_site_and_invalid_messages_do_not_break_ingest(api):
    api.push("achal/elsewhere/telemetry/n01", telemetry("n01", 1, site="elsewhere"))
    api.push("achal/jharia-demo/telemetry/n01", {"node": "n01"})  # missing everything
    api.push("achal/jharia-demo/telemetry/n02", telemetry("n02", 1))
    stats = api.get("/api/stats").json()
    assert stats["telemetry_rows"] == 1
    assert stats["ingest"]["unknown_site"] == 1 and stats["ingest"]["invalid"] == 1
