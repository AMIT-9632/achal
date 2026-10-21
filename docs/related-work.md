# Related work and how ACHAL differs

Public repos for the same problem (SIH 2026 PS SIH26025) studied as prior art. **No code was copied.**

| Project | What it does | ACHAL difference |
| --- | --- | --- |
| RONALD-REX-7/sih26025 | ESP32-S3 TinyML, LoRa, mostly underground strain | Surface mesh over the panel; cheaper node; offline siren |
| krish77-spec/MineGuard | ESP32 + BLE to Pi, local-first | LoRa multi-hop range; AI fusion with time-to-failure |
| x-ayush-x/MineGuard | Simulated 9-node mesh, 4 levels | Physics-based Knothe simulator; mesh RSSI as a movement signal |
| Appu2410/mineguard | 9-node ESP32 mesh, edge fusion | Neighbour consensus + explainable reasons in 4 languages |
| ddvkernel/KHANRAKSHAK | Adaptive LoRa mesh, digital twin | Per-node cost comparison; community-facing alerts |
| Nagasiv-cyber/minepulse_EdgeAI | LoRa honeycomb, mm/day thresholds | Inverse-velocity lead time; multi-site config |

Real systems: Amrita A-LEWS (landslides, Munnar/Sikkim), IIT Mandi low-cost landslide warning.
Survey methods compared in the deck: InSAR, GNSS, total station.
