# SPDX-License-Identifier: MIT
"""Persistence for the backend.

`PgStore` writes to TimescaleDB + PostGIS (schema in infra/db/init.sql) and is
used whenever DATABASE_URL is set. `MemoryStore` keeps a bounded history in
RAM with the same interface, for tests and DB-less demos.

Writes are idempotent: the gateway delivers at least once, so a replayed
reading is ignored by the (site, node, seq, ts) unique index.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict, deque
from datetime import datetime, timedelta

log = logging.getLogger("backend.store")

NUMERIC = ("tilt_x_deg", "tilt_y_deg", "vib_rms_g", "vib_peak_g", "crack_mm", "ext_mm", "temp_c", "batt_v",
           "rssi_dbm", "snr_db", "hops")
EARTH_R = 6_371_000.0


def parse_ts(v: str | datetime | None) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def merge_risk(rows: list[dict], risks: list[dict]) -> list[dict]:
    """Attach the latest risk at or before each reading as score/level. Both lists oldest first."""
    out, j, cur = [], 0, None
    for r in rows:
        t = parse_ts(r["ts"])
        while j < len(risks) and parse_ts(risks[j]["ts"]) <= t:
            cur = risks[j]
            j += 1
        out.append({**r, "score": cur["score"], "level": cur["level"]} if cur else dict(r))
    return out


def bucketise(rows: list[dict], bucket_s: int) -> list[dict]:
    """Average numeric fields per time bucket (memory-store twin of time_bucket)."""
    groups: dict[datetime, list[dict]] = {}
    for r in rows:
        t = parse_ts(r["ts"])
        start = datetime.fromtimestamp(t.timestamp() // bucket_s * bucket_s, tz=t.tzinfo)
        groups.setdefault(start, []).append(r)
    out = []
    for start, rs in sorted(groups.items()):
        row = {"node": rs[0]["node"], "site": rs[0]["site"], "ts": start.isoformat(), "n": len(rs)}
        for k in NUMERIC:
            vals = [x[k] for x in rs if x.get(k) is not None]
            if vals:
                row[k] = sum(vals) / len(vals)
        if any(x.get("buffered") for x in rs):
            row["buffered"] = True
        out.append(row)
    return out


class MemoryStore:
    kind = "memory"

    def __init__(self, per_node: int = 20_000, alerts: int = 1000) -> None:
        self.telemetry: dict[tuple[str, str], deque[dict]] = defaultdict(lambda: deque(maxlen=per_node))
        self.risk: dict[tuple[str, str], deque[dict]] = defaultdict(lambda: deque(maxlen=per_node))
        self.seen: set[tuple[str, str, int, str]] = set()
        self.alerts: dict[str, dict] = {}
        self.alert_order: deque[str] = deque(maxlen=alerts)
        self.events: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=5000))
        self.nodes: dict[str, list[dict]] = {}

    async def start(self, sites: dict) -> None:
        for site_id, site in sites.items():
            self.nodes[site_id] = [{"id": n, **dict(zip(("lat", "lon"), site.to_latlon(site.node_position_m(n)),
                                                        strict=True))} for n in site.node_ids()]

    async def close(self) -> None:
        pass

    async def add_telemetry(self, rows: list[dict]) -> int:
        added = 0
        for r in rows:
            key = (r["site"], r["node"], r["seq"], str(parse_ts(r["ts"])))
            if key in self.seen:
                continue
            self.seen.add(key)
            self.telemetry[(r["site"], r["node"])].append(r)
            added += 1
        return added

    async def add_risk(self, rows: list[dict]) -> None:
        for r in rows:
            self.risk[(r["site"], r["node"])].append(r)

    async def add_alert(self, a: dict) -> None:
        if a["id"] not in self.alerts:
            self.alert_order.appendleft(a["id"])
        self.alerts[a["id"]] = {**self.alerts.get(a["id"], {}), **a}

    async def ack_alert(self, alert_id: str, by: str, ts: datetime) -> tuple[dict | None, bool]:
        """Returns (alert, changed). Acknowledging twice keeps the first acknowledgement."""
        a = self.alerts.get(alert_id)
        if a is None:
            return None, False
        if a.get("acked_by"):
            return a, False
        a.update(acked_by=by, acked_ts=ts.isoformat())
        return a, True

    async def add_event(self, kind: str, site: str, msg: dict) -> None:
        self.events[f"{site}/{kind}"].appendleft(msg)

    async def get_alert(self, alert_id: str) -> dict | None:
        return self.alerts.get(alert_id)

    async def list_alerts(self, site: str, limit: int = 200, unacked: bool = False) -> list[dict]:
        out = []
        for i in self.alert_order:
            a = self.alerts.get(i)
            if a and a["site"] == site and not (unacked and a.get("acked_by")):
                out.append(a)
                if len(out) >= limit:
                    break
        return out

    async def history(self, site: str, node: str, since: datetime | None, until: datetime | None,
                      limit: int, bucket_s: int | None) -> list[dict]:
        rows = sorted(self.telemetry.get((site, node), ()), key=lambda r: parse_ts(r["ts"]))
        rows = [r for r in rows if (since is None or parse_ts(r["ts"]) >= since)
                and (until is None or parse_ts(r["ts"]) <= until)]
        if bucket_s:
            rows = bucketise(rows, bucket_s)
        rows = rows[-limit:]
        risks = sorted(self.risk.get((site, node), ()), key=lambda r: parse_ts(r["ts"]))
        return merge_risk(rows, risks)

    async def risk_history(self, site: str, node: str, since: datetime | None, until: datetime | None,
                           limit: int) -> list[dict]:
        rows = sorted(self.risk.get((site, node), ()), key=lambda r: parse_ts(r["ts"]))
        return [r for r in rows if (since is None or parse_ts(r["ts"]) >= since)
                and (until is None or parse_ts(r["ts"]) <= until)][-limit:]

    async def latest_ts(self, site: str, node: str) -> datetime | None:
        rows = self.telemetry.get((site, node))
        return max(parse_ts(r["ts"]) for r in rows) if rows else None

    async def nodes_near(self, site: str, lat: float, lon: float, radius_m: float) -> list[dict]:
        out = []
        for n in self.nodes.get(site, []):
            d = haversine_m(lat, lon, n["lat"], n["lon"])
            if d <= radius_m:
                out.append({"id": n["id"], "distance_m": round(d, 1)})
        return sorted(out, key=lambda x: x["distance_m"])

    async def stats(self, site: str) -> dict:
        rows = [r for (s, _), q in self.telemetry.items() if s == site for r in q]
        return {"store": self.kind, "telemetry_rows": len(rows),
                "buffered_rows": sum(1 for r in rows if r.get("buffered")),
                "risk_rows": sum(len(q) for (s, _), q in self.risk.items() if s == site),
                "alerts": sum(1 for a in self.alerts.values() if a["site"] == site)}


class PgStore:
    """TimescaleDB + PostGIS via asyncpg. Uses only Apache-2.0 Timescale features."""

    kind = "timescaledb"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool = None

    async def start(self, sites: dict) -> None:
        import asyncio

        import asyncpg

        for attempt in range(30):
            try:
                self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("database not ready (%s), retry %d", exc, attempt + 1)
                await asyncio.sleep(2)
        else:
            raise RuntimeError("database unreachable")
        async with self.pool.acquire() as c:
            for site_id, site in sites.items():
                rows = []
                for n in site.node_ids():
                    lat, lon = site.to_latlon(site.node_position_m(n))
                    rows.append((site_id, n, lon, lat))
                await c.executemany(
                    "INSERT INTO nodes (site, node, geom) VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)) "
                    "ON CONFLICT (site, node) DO UPDATE SET geom = EXCLUDED.geom", rows)
        log.info("connected to %s", self.kind)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def add_telemetry(self, rows: list[dict]) -> int:
        recs = [(parse_ts(r["ts"]), r["site"], r["node"], r["seq"], parse_ts(r.get("rx_ts")), r.get("hops"),
                 r.get("rssi_dbm"), r.get("snr_db"), r["tilt_x_deg"], r["tilt_y_deg"], r["vib_rms_g"],
                 r.get("vib_peak_g"), r["crack_mm"], r["ext_mm"], r.get("temp_c"), r["batt_v"],
                 bool(r.get("buffered")), json.dumps(r)) for r in rows]
        async with self.pool.acquire() as c:
            await c.executemany(
                "INSERT INTO telemetry (ts, site, node, seq, rx_ts, hops, rssi_dbm, snr_db, tilt_x_deg, tilt_y_deg, "
                "vib_rms_g, vib_peak_g, crack_mm, ext_mm, temp_c, batt_v, buffered, raw) VALUES "
                "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb) "
                "ON CONFLICT (site, node, seq, ts) DO NOTHING", recs)
        return len(recs)

    async def add_risk(self, rows: list[dict]) -> None:
        recs = [(parse_ts(r["ts"]), r["site"], r["node"], r["score"], r["level"], r.get("ttf_hours"),
                 r.get("reasons") or [], json.dumps(r.get("components") or {}), r.get("agreeing_neighbours"))
                for r in rows]
        async with self.pool.acquire() as c:
            await c.executemany(
                "INSERT INTO risk (ts, site, node, score, level, ttf_hours, reasons, components, agreeing_neighbours) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9) ON CONFLICT (site, node, ts) DO UPDATE SET "
                "score = EXCLUDED.score, level = EXCLUDED.level, ttf_hours = EXCLUDED.ttf_hours, "
                "reasons = EXCLUDED.reasons, components = EXCLUDED.components", recs)

    async def add_alert(self, a: dict) -> None:
        centroid = a.get("centroid") or {}
        async with self.pool.acquire() as c:
            await c.execute(
                "INSERT INTO alerts (id, ts, site, level, previous_level, nodes, reasons, siren, ttf_hours, centroid, "
                "raw) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, CASE WHEN $10::float8 IS NULL THEN NULL ELSE "
                "ST_SetSRID(ST_MakePoint($10, $11), 4326)::geography END, $12::jsonb) ON CONFLICT (id) DO NOTHING",
                a["id"], parse_ts(a["ts"]), a["site"], a["level"], a.get("previous_level"), a.get("nodes") or [],
                a.get("reasons") or [], a.get("siren"), a.get("ttf_hours"), centroid.get("lon"), centroid.get("lat"),
                json.dumps(a))

    @staticmethod
    def _alert(row) -> dict:
        a = json.loads(row["raw"])
        if row["acknowledged_by"]:
            a.update(acked_by=row["acknowledged_by"], acked_ts=row["acknowledged_at"].isoformat())
        return a

    async def ack_alert(self, alert_id: str, by: str, ts: datetime) -> tuple[dict | None, bool]:
        async with self.pool.acquire() as c:
            row = await c.fetchrow(
                "UPDATE alerts SET acknowledged_by = $2, acknowledged_at = $3 "
                "WHERE id = $1 AND acknowledged_by IS NULL RETURNING raw, acknowledged_by, acknowledged_at",
                alert_id, by, ts)
            if row:
                return self._alert(row), True
            row = await c.fetchrow("SELECT raw, acknowledged_by, acknowledged_at FROM alerts WHERE id = $1", alert_id)
            return (self._alert(row), False) if row else (None, False)

    async def get_alert(self, alert_id: str) -> dict | None:
        async with self.pool.acquire() as c:
            row = await c.fetchrow("SELECT raw, acknowledged_by, acknowledged_at FROM alerts WHERE id = $1", alert_id)
        return self._alert(row) if row else None

    async def list_alerts(self, site: str, limit: int = 200, unacked: bool = False) -> list[dict]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT raw, acknowledged_by, acknowledged_at FROM alerts WHERE site = $1 "
                "AND ($3::bool IS FALSE OR acknowledged_by IS NULL) ORDER BY ts DESC LIMIT $2", site, limit, unacked)
        return [self._alert(r) for r in rows]

    async def add_event(self, kind: str, site: str, msg: dict) -> None:
        ts = parse_ts(msg.get("ts"))
        async with self.pool.acquire() as c:
            if kind == "siren":
                await c.execute("INSERT INTO siren_events (ts, site, siren, on_, level, source, raw) "
                                "VALUES (COALESCE($1, now()), $2, $3, $4, $5, $6, $7::jsonb)",
                                ts, site, msg.get("id"), bool(msg.get("on")), msg.get("level"), msg.get("source"),
                                json.dumps(msg))
            elif kind == "gateway":
                await c.execute("INSERT INTO gateway_status (ts, site, gateway, online, buffered, raw) "
                                "VALUES (COALESCE($1, now()), $2, $3, $4, $5, $6::jsonb)",
                                ts, site, msg.get("gateway"), msg.get("online"), msg.get("buffered"), json.dumps(msg))
            elif kind == "delivery":
                await c.execute("INSERT INTO alert_deliveries (ts, site, alert_id, recipient, channel, lang, status, "
                                "raw) VALUES (COALESCE($1, now()), $2, $3, $4, $5, $6, $7, $8::jsonb)",
                                ts, site, msg.get("alert_id"), msg.get("recipient"), msg.get("channel"),
                                msg.get("lang"), msg.get("status"), json.dumps(msg))

    async def history(self, site: str, node: str, since: datetime | None, until: datetime | None,
                      limit: int, bucket_s: int | None) -> list[dict]:
        async with self.pool.acquire() as c:
            if bucket_s:
                avgs = ", ".join(f"avg({k})::float8 AS {k}" for k in NUMERIC)
                rows = await c.fetch(
                    f"SELECT * FROM (SELECT time_bucket(make_interval(secs => $5), ts) AS ts, count(*) AS n, "
                    f"bool_or(buffered) AS buffered, {avgs} FROM telemetry WHERE site = $1 AND node = $2 "
                    "AND ($3::timestamptz IS NULL OR ts >= $3) AND ($4::timestamptz IS NULL OR ts <= $4) "
                    "GROUP BY 1 ORDER BY 1 DESC LIMIT $6) b ORDER BY ts", site, node, since, until, bucket_s, limit)
                data = [{"site": site, "node": node, **{k: v for k, v in dict(r).items() if v is not None},
                         "ts": r["ts"].isoformat()} for r in rows]
            else:
                rows = await c.fetch(
                    "SELECT raw FROM (SELECT ts, raw FROM telemetry WHERE site = $1 AND node = $2 "
                    "AND ($3::timestamptz IS NULL OR ts >= $3) AND ($4::timestamptz IS NULL OR ts <= $4) "
                    "ORDER BY ts DESC LIMIT $5) t ORDER BY ts", site, node, since, until, limit)
                data = [json.loads(r["raw"]) for r in rows]
            if not data:
                return []
            risks = await c.fetch(
                "SELECT ts, score, level FROM risk WHERE site = $1 AND node = $2 AND ts <= $4 AND ts >= $3 - interval "
                "'1 hour' ORDER BY ts", site, node, parse_ts(data[0]["ts"]), parse_ts(data[-1]["ts"]))
        return merge_risk(data, [{"ts": r["ts"], "score": r["score"], "level": r["level"]} for r in risks])

    async def risk_history(self, site: str, node: str, since: datetime | None, until: datetime | None,
                           limit: int) -> list[dict]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT * FROM (SELECT ts, score, level, ttf_hours, reasons, components, agreeing_neighbours FROM risk "
                "WHERE site = $1 AND node = $2 AND ($3::timestamptz IS NULL OR ts >= $3) "
                "AND ($4::timestamptz IS NULL OR ts <= $4) ORDER BY ts DESC LIMIT $5) r ORDER BY ts",
                site, node, since, until, limit)
        return [{"site": site, "node": node, "ts": r["ts"].isoformat(), "score": r["score"], "level": r["level"],
                 "ttf_hours": r["ttf_hours"], "reasons": list(r["reasons"] or []),
                 "components": json.loads(r["components"] or "{}"), "agreeing_neighbours": r["agreeing_neighbours"]}
                for r in rows]

    async def latest_ts(self, site: str, node: str) -> datetime | None:
        async with self.pool.acquire() as c:
            return await c.fetchval("SELECT max(ts) FROM telemetry WHERE site = $1 AND node = $2", site, node)

    async def nodes_near(self, site: str, lat: float, lon: float, radius_m: float) -> list[dict]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT node, ST_Distance(geom, ST_SetSRID(ST_MakePoint($3, $2), 4326)::geography) AS d FROM nodes "
                "WHERE site = $1 AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint($3, $2), 4326)::geography, $4) "
                "ORDER BY d", site, lat, lon, radius_m)
        return [{"id": r["node"], "distance_m": round(r["d"], 1)} for r in rows]

    async def stats(self, site: str) -> dict:
        async with self.pool.acquire() as c:
            r = await c.fetchrow(
                "SELECT (SELECT count(*) FROM telemetry WHERE site = $1) AS t, "
                "(SELECT count(*) FROM telemetry WHERE site = $1 AND buffered) AS b, "
                "(SELECT count(*) FROM risk WHERE site = $1) AS r, (SELECT count(*) FROM alerts WHERE site = $1) AS a",
                site)
        return {"store": self.kind, "telemetry_rows": r["t"], "buffered_rows": r["b"], "risk_rows": r["r"],
                "alerts": r["a"]}


def window(latest: datetime | None, minutes: float | None, since: datetime | None,
           until: datetime | None) -> tuple[datetime | None, datetime | None]:
    """`minutes` counts back from the newest reading, not the wall clock: simulated time runs fast."""
    if minutes is not None and since is None and latest is not None:
        since = (until or latest) - timedelta(minutes=minutes)
    return since, until
