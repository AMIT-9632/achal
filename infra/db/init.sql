-- SPDX-License-Identifier: MIT
-- TimescaleDB + PostGIS schema. W2 wires the backend to it.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS nodes (
  site TEXT NOT NULL,
  node TEXT NOT NULL,
  geom GEOGRAPHY(POINT, 4326),
  installed_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (site, node)
);

CREATE TABLE IF NOT EXISTS telemetry (
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  node TEXT NOT NULL,
  seq BIGINT,
  hops SMALLINT,
  rssi_dbm REAL,
  tilt_x_deg DOUBLE PRECISION,
  tilt_y_deg DOUBLE PRECISION,
  vib_rms_g DOUBLE PRECISION,
  crack_mm DOUBLE PRECISION,
  ext_mm DOUBLE PRECISION,
  temp_c REAL,
  batt_v REAL,
  buffered BOOLEAN,
  raw JSONB
);
SELECT create_hypertable('telemetry', 'ts', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS telemetry_dedup ON telemetry (site, node, seq, ts);

CREATE TABLE IF NOT EXISTS risk (
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  node TEXT NOT NULL,
  score REAL,
  level TEXT,
  ttf_hours REAL,
  reasons TEXT[],
  components JSONB
);
SELECT create_hypertable('risk', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS alerts (
  id UUID PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  site TEXT NOT NULL,
  level TEXT NOT NULL,
  previous_level TEXT,
  nodes TEXT[],
  reasons TEXT[],
  siren BOOLEAN,
  acknowledged_by TEXT,
  acknowledged_at TIMESTAMPTZ
);
