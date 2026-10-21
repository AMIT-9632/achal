-- SPDX-License-Identifier: MIT
-- TimescaleDB + PostGIS schema for the ACHAL backend (services/backend/src/store.py).
-- Uses only Apache-2.0 TimescaleDB features (hypertables, time_bucket); no TSL-licensed
-- compression or continuous aggregates. Idempotent: safe to run again.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS nodes (
  site TEXT NOT NULL,
  node TEXT NOT NULL,
  geom GEOGRAPHY(POINT, 4326),
  installed_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (site, node)
);
CREATE INDEX IF NOT EXISTS nodes_geom ON nodes USING GIST (geom);

-- One row per reading. The gateway delivers at least once; the unique index makes replays harmless.
CREATE TABLE IF NOT EXISTS telemetry (
  ts TIMESTAMPTZ NOT NULL,          -- sample time at the node
  site TEXT NOT NULL,
  node TEXT NOT NULL,
  seq BIGINT NOT NULL,
  rx_ts TIMESTAMPTZ,                -- time the gateway received it
  hops SMALLINT,
  rssi_dbm REAL,
  snr_db REAL,
  tilt_x_deg DOUBLE PRECISION,
  tilt_y_deg DOUBLE PRECISION,
  vib_rms_g DOUBLE PRECISION,
  vib_peak_g DOUBLE PRECISION,
  crack_mm DOUBLE PRECISION,
  ext_mm DOUBLE PRECISION,
  temp_c REAL,
  batt_v REAL,
  buffered BOOLEAN,
  raw JSONB                         -- full message, including neighbour_rssi, path and synthetic truth
);
SELECT create_hypertable('telemetry', 'ts', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS telemetry_dedup ON telemetry (site, node, seq, ts);
CREATE INDEX IF NOT EXISTS telemetry_node_ts ON telemetry (site, node, ts DESC);

CREATE TABLE IF NOT EXISTS risk (
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  node TEXT NOT NULL,
  score DOUBLE PRECISION,
  level TEXT,
  ttf_hours DOUBLE PRECISION,
  reasons TEXT[],
  components JSONB,
  agreeing_neighbours SMALLINT
);
SELECT create_hypertable('risk', 'ts', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS risk_node_ts ON risk (site, node, ts);

CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  level TEXT NOT NULL,
  previous_level TEXT,
  nodes TEXT[],
  reasons TEXT[],
  siren BOOLEAN,
  ttf_hours DOUBLE PRECISION,
  centroid GEOGRAPHY(POINT, 4326),
  raw JSONB,
  acknowledged_by TEXT,
  acknowledged_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS alerts_site_ts ON alerts (site, ts DESC);

CREATE TABLE IF NOT EXISTS siren_events (
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  siren TEXT NOT NULL,
  on_ BOOLEAN NOT NULL,
  level TEXT,
  source TEXT,                      -- gateway (local rules) or cloud (risk engine)
  raw JSONB
);
SELECT create_hypertable('siren_events', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS gateway_status (
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  gateway TEXT,
  online BOOLEAN,
  buffered INTEGER,
  raw JSONB
);
SELECT create_hypertable('gateway_status', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS alert_deliveries (
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  alert_id TEXT,
  recipient TEXT,
  channel TEXT,
  lang TEXT,
  status TEXT,
  raw JSONB
);
SELECT create_hypertable('alert_deliveries', 'ts', if_not_exists => TRUE);
