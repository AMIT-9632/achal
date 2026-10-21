# MQTT topics and message contracts

All services talk through Mosquitto. JSON payloads follow the schemas in `schemas/`.
Breaking changes need a new version (`telemetry.v2.json`) and an ADR.

| Topic | Publisher | Schema | Notes |
| --- | --- | --- | --- |
| `achal/{site}/telemetry/{node}` | gateway | `telemetry.v1.json` | One reading per node per sample period |
| `achal/{site}/risk/{node}` | risk-engine | `risk.v1.json` | Fused score + level per node, every evaluation tick (10 min of sensor time) |
| `achal/{site}/risk/grid` | risk-engine | `risk-grid.v1.json` | IDW risk raster for the map (retained), every evaluation tick |
| `achal/{site}/alert` | risk-engine | `alert.v1.json` | Only on level change |
| `achal/{site}/alert/ack` | backend | `{id, by, ts}` | Operator acknowledged an alert; alerts service stops escalating it |
| `achal/{site}/alert/delivery` | alerts | `{alert_id, recipient, channel, lang, tier, status, ts}` | One record per message attempt (no phone numbers) |
| `achal/{site}/gateway/status` | gateway | `{online, buffered, ts}` + counters, `silent_nodes`, `sirens` | Retained, every 5 s; fields in [`gateway.md`](gateway.md) |
| `achal/{site}/siren/{id}/cmd` | gateway, risk-engine | `{on, level, ts}` (+ `source`, `nodes`, `reasons` from the gateway) | Gateway also drives sirens locally when offline; its commands carry `source: "gateway"` and are queued ahead of telemetry |

The radio link between field nodes and the gateway is simulated as UDP
(`field-sim` -> `gateway:1700/udp`), carrying the same JSON as telemetry minus `rx_ts`.

Reason codes (translated in `services/dashboard/src/i18n/*.json` and `services/alerts/src/templates/`):
`tilt_rate_high`, `crack_opening`, `ext_stretch`, `vibration_burst`, `iforest_anomaly`,
`neighbour_consensus`, `ttf_short`, `node_silent`, `sensor_fault_suspected`, `ground_heating`
(plus `level_change` on alerts with no other reason). RiskAssessment `components` keys: `rules`,
`iforest`, `trend`, `consensus`, `fused`. See `docs/risk-engine.md`.
