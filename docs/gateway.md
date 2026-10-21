# Edge gateway

Code: [`services/gateway/src/gateway.py`](../services/gateway/src/gateway.py),
[`rules.py`](../services/gateway/src/rules.py) · tests:
[`test_gateway.py`](../services/gateway/tests/test_gateway.py),
[`test_buffer.py`](../services/gateway/tests/test_buffer.py) · demo:
[`scripts/demo_uplink_outage.sh`](../scripts/demo_uplink_outage.sh).

The gateway plays the Raspberry Pi + LoRa concentrator at the edge of the site. Its job is to keep
warning people when everything upstream of it (internet, cloud broker, risk engine) is gone.

## What it does

1. **Receives** node frames (UDP stands in for the radio), validates them against `telemetry.v1`,
   rejects other sites' frames, stamps `rx_ts`.
2. **Drops duplicates.** The mesh can deliver one reading twice (lost ACK, second path). The key is
   (site, node, seq, ts), stored in SQLite so a gateway reboot does not let old copies back in.
3. **Store and forward.** Every message goes into a SQLite outbox (WAL mode) *before* anything else and
   is deleted only when the broker returns a QoS 1 PUBACK for it. A dropped connection puts in-flight
   messages back in the queue. Replay keeps arrival order; siren commands jump the queue. Delivery is
   at least once, and the backend's unique index on (site, node, seq, ts) makes it effectively once.
4. **Local siren rules** (`rules.py`), independent of the AI and the uplink:
   - each node learns a baseline from its first 10 readings;
   - a node is *alarmed* when tilt change ≥ 0.10°, crack opening ≥ 2 mm, extensometer stretch ≥ 5 mm,
     or vibration peak ≥ 0.05 g, in at least 2 of its last 3 readings (a single spike, typical of a
     faulty sensor, is ignored);
   - sirens sound only when at least 2 alarmed nodes are grid neighbours within 30 minutes (one node
     alone never sounds a siren); critical if any of them is 3× past a threshold, otherwise warning;
   - sirens within 400 m of the alarmed patch go on, never step down while on, and go off after
     60 minutes without consensus.
   Thresholds are starting values for the synthetic panel, overridable with
   `GATEWAY_RULES='{"tilt_deg": 0.08}'`, and are to be calibrated on the W1 scenarios
   (pillar collapse must trigger, faults must not). Times are sensor time, so accelerated runs work.
5. **Relays cloud siren commands** from the risk engine to the sirens, so either layer can sound them.
6. **Health**: `GET :9100/status` (JSON) and `GET :9100/metrics` (Prometheus), plus the retained
   `achal/{site}/gateway/status` message every 5 s.

## Status and metrics

`/status` and the MQTT status message carry `online`, `buffered`, `ts` (the v0.1 fields) plus
`gateway`, `uptime_s`, `max_buffered`, `inflight`, the counters below, `nodes_heard`,
`silent_nodes` (not heard for 5 sample periods) and `sirens` ({id: {on, level, source}}).

| Counter | Meaning |
| --- | --- |
| `frames_rx` | frames heard from the radio |
| `frames_bad` | invalid JSON/schema or another site's frame |
| `frames_duplicate` | second copies dropped |
| `readings_unique` | readings accepted into the outbox |
| `published` | messages acknowledged by the broker |
| `replayed` | readings sent that had been buffered while offline |
| `siren_commands` | siren on/off actions (local and cloud) |
| `uplink_drops` | in-flight messages re-queued after a disconnect |

Prometheus also gets per-node `achal_node_rssi_dbm` and `achal_node_hops`, per-siren
`achal_gateway_siren_on`, and `achal_gateway_uplink_online`.

## Uplink outage demo (measured)

`scripts/demo_uplink_outage.sh 120` on the full Docker stack (TimescaleDB, Mosquitto, field-sim at
×60, so 120 s of outage is 2 h of field time), 21 Oct 2026:

```
readings the gateway received during the test: 4225
readings stored in TimescaleDB:                 4225
peak gateway buffer during the outage:          3325
ZERO READINGS LOST
```

What makes that true end to end: the PUBACK-gated outbox above, plus a persistent MQTT session for the
backend (`clean_session=False`, `max_queued_messages 200000` in `infra/mosquitto/mosquitto.conf`) so
readings the gateway replays while the backend is still reconnecting are queued for it by the broker.

## Before a real deployment

- Mosquitto with TLS and per-device credentials (`allow_anonymous false`), gateway client certificate.
- Read-only root filesystem on the Pi, buffer on a high-endurance SD card or USB SSD, watchdog.
- Battery-backed siren controller wired (or LoRa-linked) to the gateway, tested weekly.
