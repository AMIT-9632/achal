# Architecture

## Data path

1. **field-sim** models a 100 m × 100 m bord-and-pillar panel at ~60 m depth with a 5 × 5 node grid
   (`config/sites/jharia-demo.yaml`). Each virtual node reports tilt (2 axes), vibration RMS/peak,
   crack-gauge opening, wire-extensometer stretch to its neighbour, temperature, battery and the
   RSSI it hears from neighbours, once per sample period. Ground movement comes from a Knothe
   influence-function trough, accelerating pillar creep and pothole collapse, or an underground
   fire, with realistic sensor noise, temperature drift, battery and faults; every reading carries
   synthetic ground truth. See [`field-sim-model.md`](field-sim-model.md).
2. The **mesh model** routes each frame over up to `max_hops` relays to the gateway, with packet
   loss, node failure and route repair. The radio is stood in for by UDP.
3. **gateway** (Raspberry Pi role) validates frames, stamps receive time, writes them to a SQLite
   outbox and drains the outbox to MQTT whenever the broker is reachable. Nothing is lost during an
   uplink cut. It can trigger sirens locally from a rule check, with no cloud dependency.
4. **risk-engine** scores each reading and each neighbourhood, publishes a fused 0–1 risk score,
   a level (normal, watch, warning, critical), reason codes and a time-to-failure estimate, and
   raises an alert on level changes.
5. **alerts** renders each alert in every site language and sends SMS / email / push.
6. **backend** keeps history (TimescaleDB + PostGIS) and serves REST + WebSocket.
7. **dashboard** shows the live node map, levels, alerts and reasons, in four languages.

## Design decisions

- One message contract (`schemas/*.v1.json`) mirrored by `achal_common.models`; services are
  decoupled through MQTT so each can be rebuilt or replaced alone. See `docs/adr/`.
- Offline first: the site must keep warning people with no internet (gateway buffer + local siren).
- Multi-site by configuration: a new coalfield is a new YAML file, not new code.
- Explainability: every risk score carries reason codes, translated for residents and operators.

## Scaling path

| Scale | Change |
| --- | --- |
| 1 panel, 25 nodes | As built: one gateway, one broker |
| 1 colliery, ~500 nodes | Several gateways, one broker per colliery, TimescaleDB retention policies |
| Many coalfields | Broker bridge to a cloud broker or Kafka, multi-tenant backend, per-site config |
