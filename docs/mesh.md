# Wireless surface mesh (simulated LoRa, IN865)

Code: [`services/field-sim/src/mesh.py`](../services/field-sim/src/mesh.py) · study:
[`mesh_study.py`](../services/field-sim/src/mesh_study.py) · results: [`mesh-study.md`](mesh-study.md),
[`data/mesh-study/results.json`](../data/mesh-study/results.json) · tests:
[`test_mesh.py`](../services/field-sim/tests/test_mesh.py). Everything here is **synthetic**: no radio
has been flown over Jharia yet. The model exists so that the network design (spacing, gateway
placement, hop limit, retries) can be argued with numbers before hardware is bought.

## Why a mesh and not a star

A single-hop LoRaWAN star needs every node to reach the gateway directly. On a 100 m panel with the
gateway at its edge that is easy, and the study shows both designs deliver everything there. It stops
being easy at colliery scale, when the gateway has to stand on safe, stable ground away from the
workings (here 200 m outside a 315 m × 315 m block of 100 nodes) and the antennas sit 1 m above
broken, built-up ground:

- far nodes in a star must climb to SF10–SF12, which multiplies time on air (72 ms at SF7, 1.81 s at
  SF12 for our 31-byte frame), battery drain and collisions, and a few nodes still cannot be heard
  at all;
- in the mesh every node stays on SF7 and relays through a neighbour, so the far edge is covered at
  the same energy cost as the near edge, and the network routes around nodes that a pothole, fire or
  theft takes out.

## What is modelled

| Layer | Model | Value (baseline profile) | Source |
| --- | --- | --- | --- |
| Band | IN865, three default channels | 865.0625 / 865.4025 / 865.985 MHz | LoRaWAN Regional Parameters RP002, IN865 |
| Modulation | LoRa, BW 125 kHz, CR 4/5, 8-symbol preamble, explicit header, CRC | SF7 in the mesh; star picks SF7–12 per node (ADR-like) | Semtech SX1276 datasheet |
| Time on air | Semtech formula, low-data-rate optimisation above 16 ms symbols | 31 B: 71.9 ms at SF7, 1.81 s at SF12 | Semtech AN1200.13 |
| Frame | 31-byte binary reading (ids, seq, tilt ×2, vibration rms/peak, crack, stretch, temperature, battery, 4 relay ids, CRC) | fits the 51-byte IN865 SF12 limit | `mesh.FRAME` |
| Transmit power | 14 dBm (below the IN865 limit, to save battery) | node whip 2.15 dBi, gateway collinear 5 dBi on a mast, 0.5 dB cable each end | design choice |
| Path loss | log-distance, PL(d) = PL(1 m) + 10·n·log10(d) | n = 4.0 for 1 m antennas over spoil heaps and houses; PL(1 m) = free space at 865 MHz (31.2 dB) | Rappaport, *Wireless Communications*, ch. 4 (n = 4–6 obstructed) |
| Shadowing | static per link, log-normal, reciprocal | σ = 7 dB | Rappaport ch. 4 |
| Fast fading | per packet, Gaussian in dB | σ = 3 dB | modelling choice |
| Receiver | sensitivity and demodulation SNR floor per SF; noise floor −174 + 10·log10(BW) + NF | −123 dBm at SF7 … −137 dBm at SF12; NF 6 dB | SX1276 datasheet; Semtech AN1200.22 |
| Collisions | pure ALOHA on the shared channel from the offered load (data + ACK frames), no capture effect (pessimistic) | — | Abramson (1970) |
| Listen before talk | mesh nodes run channel-activity detection; only hidden senders and starts within 2 ms still collide. The star baseline is LoRaWAN class A, which does not listen | CAD window 2 ms | SX1276 CAD |
| Link layer | unicast with ACK, up to 3 retries per hop; a lost ACK makes the sender retry and the receiver get the frame twice, which reaches the gateway as a duplicate | — | — |
| Routing | gateway-rooted tree that minimises expected transmissions (ETX = 1 / (PRR_fwd · PRR_ack)), at most `max_hops` hops, links weaker than sensitivity + 3 dB not used; rebuilt from beacons every 300 s | `max_hops` from the site YAML (4 demo, 5 colliery) | De Couto et al. (2003), ETX; RPL (RFC 6550) for the rank idea |
| Failure and repair | chosen nodes die without warning at `fail_at_s`; neighbours only notice when a whole round of retries gets no ACK, then blacklist the parent and switch to the best alternate parent with a strictly lower rank (no loops). Dead nodes leave routing tables after missing 3 beacons | random, or one contiguous `cluster` (a pothole) | — |
| Relay duplicates | each relay forwards a (source, seq) once | — | — |

Each delivered reading carries `hops`, `path` (source first, then relays), `rssi_dbm` and `snr_db` of
the last hop into the gateway, and `neighbour_rssi` (beacon RSSI heard from up to 8 live neighbours,
strongest first), all in `telemetry.v1`. The risk engine can use a sudden drop in neighbour RSSI as
a relative-movement or obstruction signal.

## Results (10 seeds × 12 h per row, failures 1 h in)

Full table: [`mesh-study.md`](mesh-study.md). Headline numbers for the colliery block, 30 % of the
nodes dead without warning:

| Radio profile | Design | Packet delivery (mean, worst run) | Worst single node | Airtime per node per day |
| --- | --- | --- | --- | --- |
| baseline | **mesh** | **100.000 %, 100.000 %** | 100 % | 138 s (0.16 % duty) |
| baseline | star | 98.85 %, 97.88 % | 0 % in 5 of 10 seeds (unreachable) | 321 s |
| harsh (n = 4.4, σ = 9 dB) | **mesh** | **100.000 %, 100.000 %** | 100 % | 191 s |
| harsh | star | 76.41 %, 65.81 % | 0 % | 1,149 s |

Across all 70 colliery runs per design (4.25 million readings each), the mesh lost **0** readings
with the baseline radio and **3** with the harsh one; the star lost 65,471 and 1,048,896. On the
25-node demo panel both designs deliver ≥ 99.99 %, as expected for 100 m links. The KPI target
"≥ 95 % delivery with 30 % of nodes down" is met with a wide margin in this model.

## Limits of the model (what a jury should push on)

- Propagation is a statistical model, not a terrain model. Real Jharia links need a site survey
  (RSSI walk test with one node and the gateway), which is the first hardware task for the next round.
- Routing assumes link quality is known from beacons; real ETX estimates are noisy and lag.
- No interference from other 865 MHz users, no gateway outage (that is the gateway buffer's job, see
  [`gateway.md`](gateway.md)), no clock drift.
- Retries hide loss at the cost of airtime, energy and latency (p95 0.6–0.8 s per reading in the mesh).
  Those costs are reported, not hidden.
- Node ids are `n00`–`n99` in schema v1, so one site has at most 100 nodes; larger areas are more
  sites, each with its own gateway.

## Run it

```bash
# full study (about 10 minutes on 4 cores)
PYTHONPATH=services/field-sim/src python services/field-sim/src/mesh_study.py --seeds 10 --hours 12
# in the live stack: 30 % of nodes die one simulated hour in, as a contiguous patch
MESH_FAIL_FRACTION=0.3 MESH_FAIL_PATTERN=cluster docker compose up -d field-sim
# single-hop baseline instead of the mesh
MESH_MODE=star docker compose up -d field-sim
```
