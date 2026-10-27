# ACHAL — Mine subsidence early warning over a wireless surface mesh

**ACHAL** (अचल, "unmoving") is an open-source, low-cost, AI-enabled system that watches the
ground above shallow underground coal workings and warns people *before* it gives way.
It is Team ACHAL's (NIT Jamshedpur) entry to **Hack for a Social Cause, VBYLD 2027**,
category 7: AI, GIS & Emerging Technologies.

> **Status: v0.1 scaffold (simulation only).** Every service runs end to end on synthetic
> data from a Jharia-type bord-and-pillar panel. Real ESP32 + LoRa hardware comes in later rounds;
> its reference firmware and bill of materials live in `firmware/` and `hardware/`.
> All data shown is **synthetic**.

## Why

Jharia coalfield (Dhanbad, Jharkhand) has old, shallow bord-and-pillar workings and
underground fires beneath dense settlements, roads and rail. Today subsidence is mostly
found by periodic surveys or after the damage. ACHAL puts a dense mesh of ₹-cheap sensor
nodes on the surface, fuses their readings with AI, and sounds a siren in the village even
when the internet is down.

## How it works

```
 ESP32 nodes ──LoRa mesh──▶ Gateway (Pi) ──MQTT──▶ Risk engine ──▶ Alerts (SMS / email / push, 4 languages)
 tilt, vibration,  multi-hop,   SQLite buffer,          rules + Isolation Forest       │
 crack, stretch,   self-healing local siren              + consensus + time-to-failure  ▼
 RSSI                                       └──▶ Backend (FastAPI, TimescaleDB/PostGIS) ──▶ Dashboard (React + Leaflet)
```

| Layer | This round (simulated) | Code |
| --- | --- | --- |
| Sensor nodes | Python virtual nodes, Knothe influence-function ground model | `services/field-sim` |
| Wireless surface mesh | Simulated multi-hop LoRa with loss, failures, RSSI | `services/field-sim` (mesh module) |
| Gateway / edge | SQLite store-and-forward, offline siren | `services/gateway` |
| Messaging | Mosquitto MQTT | `infra/mosquitto` |
| Backend | FastAPI REST + WebSocket, TimescaleDB + PostGIS | `services/backend`, `infra/db` |
| AI risk engine | Rules, Isolation Forest, multi-node consensus, inverse-velocity TTF | `services/risk-engine` |
| GIS + dashboard | React, Leaflet, OpenStreetMap | `services/dashboard` |
| Alerts | Hindi, Bengali, Santali, English; SMS, email | `services/alerts` |
| Hardware reference | ESP32 + SX1276 + MPU6050 + VL53L0X firmware, BOM, power budget | `firmware/`, `hardware/` |
| Hardware simulation | Datasheet-anchored node, solar and network models, Wokwi node, ns-3 cross-check, replay dashboard | `services/hw-sim`, `firmware/wokwi`, [`docs/hardware-sim/report.md`](docs/hardware-sim/report.md) |

Message contracts: [`schemas/`](schemas) and [`docs/mqtt-topics.md`](docs/mqtt-topics.md).
Architecture: [`docs/architecture.md`](docs/architecture.md).

## Run it

Requires Docker with Compose v2.

```bash
git clone https://github.com/<owner>/achal.git && cd achal
cp .env.example .env            # optional: SMTP / Twilio for real alerts
docker compose up --build
```

- Dashboard: http://localhost:8080 (language switch top right)
- API: http://localhost:8000/docs
- Change scenario: `SCENARIO=pillar_collapse docker compose up field-sim`
  (`normal`, `trough`, `pillar_collapse`, `faults`, `fire`; `SIM_SEED`, `SIM_SPEED`, `SIM_PERIOD_S`).
  Labelled sample runs are in [`data/samples/`](data/samples); the model is in
  [`docs/field-sim-model.md`](docs/field-sim-model.md).
- TimescaleDB + PostGIS starts by default; gateway health at http://localhost:9100/status and `/metrics`
- Uplink-outage demo (zero readings lost): `scripts/demo_uplink_outage.sh 120`
- Mesh with 30% of nodes dying: `MESH_FAIL_FRACTION=0.3 docker compose up -d field-sim` ([docs/mesh.md](docs/mesh.md))

### Develop one service without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "libs/achal-common[dev]"
pytest libs services
cd services/dashboard && npm ci && npm run dev
```

## Repository layout

```
config/sites/     one YAML per coalfield (add a site = add a file)
schemas/          JSON Schemas for telemetry, risk and alerts
libs/achal-common shared models, topics, config, MQTT helper
services/         field-sim, gateway, backend, risk-engine, alerts, dashboard, hw-sim
infra/            broker config, DB schema, shared Dockerfile
firmware/         reference ESP32 + LoRa code (not yet flashed)
hardware/         BOM and per-node cost, power budget
data/samples/     sample runs with ground truth, for the jury
docs/             architecture, KPIs, IP register, AI-use disclosure, ADRs
pitch/            deck outline, video script, jury Q&A bank
```

## KPIs

Measured from simulation runs by `services/risk-engine` (see [`docs/kpis.md`](docs/kpis.md)):
warning lead time, recall of subsidence events, false alarms per node per month,
packet delivery with 30% of nodes down, cost per node and per hectare, people covered.

## Open source, licences and AI use

- Code: [MIT](LICENSE). Every third-party library, dataset, map tile and font is listed with its
  licence in [`docs/ip-register.md`](docs/ip-register.md).
- Map data © OpenStreetMap contributors (ODbL).
- Prior art and how ACHAL differs: [`docs/related-work.md`](docs/related-work.md).
- AI assistance used in building this project is disclosed in [`docs/ai-use.md`](docs/ai-use.md).

## Team

See [`CONTRIBUTORS.md`](CONTRIBUTORS.md).
