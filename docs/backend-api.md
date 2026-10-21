# Backend API

FastAPI service in [`services/backend`](../services/backend); interactive docs at
http://localhost:8000/docs. One backend serves every site in `config/sites/` (currently
`jharia-demo`, 25 nodes, and `jharia-colliery`, 100 nodes). Every query endpoint takes `?site=`,
defaulting to `SITE_ID`. All data in this build is synthetic.

| Method & path | Returns |
| --- | --- |
| `GET /health` | `{ok, site, sites, store, mqtt}` |
| `GET /api/sites` | every configured site with node count, gateway, languages, people covered |
| `GET /api/site` | site config, node and siren positions (lat/lon), gateway position |
| `GET /api/nodes` | latest `telemetry` and `risk` per node, `gateway` status, risk `grid`, `sirens` by id |
| `GET /api/nodes/geojson` | latest state per node as a GeoJSON FeatureCollection (for QGIS / Leaflet) |
| `GET /api/nodes/near?lat&lon&radius_m` | nodes within a radius, nearest first (PostGIS `ST_DWithin`) |
| `GET /api/nodes/{id}/history?minutes|from&to&limit&bucket_s` | telemetry oldest first, with the `score`/`level` in force at each reading; `minutes` counts back from the newest reading (simulated time runs fast); `bucket_s` averages per bucket (`time_bucket`) |
| `GET /api/nodes/{id}/risk?...` | risk history (score, level, ttf, reasons, components) |
| `GET /api/alerts?limit&unacked` | newest first, with `acked_by` / `acked_ts` once acknowledged |
| `POST /api/alerts/{id}/ack` `{"by": "name"}` | acknowledges once (a second ack keeps the first), broadcasts `alert_ack`, publishes `achal/{site}/alert/ack`. Needs `Authorization: Bearer $OPERATOR_TOKEN` when that is set |
| `GET /api/gateway` | latest gateway status |
| `GET /api/deliveries` | latest alert delivery records from the alerts service |
| `GET /api/stats` | row counts (used by the outage demo to prove nothing was lost) |
| `WS /ws?site=` | every message as `{kind, site, data}`; `site=*` for all sites |

WebSocket kinds: `telemetry`, `risk`, `grid`, `alert`, `alert_ack`, `delivery`, `gateway`,
`siren` (data gets `id` = siren id from the topic).

## Storage

`DATABASE_URL` set → TimescaleDB + PostGIS (`infra/db/init.sql`): hypertables `telemetry`, `risk`,
`siren_events`, `gateway_status`, `alert_deliveries`; `alerts`; `nodes` with a geography point per
node. Only Apache-2.0 TimescaleDB features are used. Unset → a bounded in-memory store with the same
behaviour (tests, quick demos). Writes are batched every 0.5 s and idempotent: replayed readings hit
the `(site, node, seq, ts)` unique index. Messages that do not match schema v1 are still stored raw
and counted in `/api/stats` → `ingest.invalid`, so a contract slip shows up without losing data.

The backend's MQTT session is persistent (`clean_session=False`), so what the gateway replays while
the backend reconnects is queued by the broker rather than dropped.
