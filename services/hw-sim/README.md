# ACHAL hardware simulator (`services/hw-sim`)

A research-grade simulation of the ACHAL surface node and its network, built step by step from the
team's simulation guide: ESP32 + MPU6050 + VL53L0X + 18650/TP4056/solar nodes, a Raspberry Pi
gateway, and the three network options (A: Wi-Fi mesh, B: LoRaWAN, C: Wi-Fi mesh with a long-range
uplink). Results, methods and limits are in [`docs/hardware-sim/report.md`](../../docs/hardware-sim/report.md).

```
 scenarios.py ──▶ components/ (MPU6050, VL53L0X, power, ESP32) ──▶ node.py ──▶ network.py ──▶ gateway.py ──▶ dashboard/
 labelled ground   datasheet-anchored sensor and energy models     firmware DSP   A / B / C      Pi alert logic   Streamlit
 truth + faults                                                    (C, ctypes)
```

The node's signal processing is not re-written in Python. `achal_hwsim.dsp` compiles
`firmware/lib/achal_dsp/achal_dsp.c` (the file the ESP32 node and the Wokwi node build) and calls it
through ctypes, so the simulator, Wokwi and the real node make the same decision on the same input.

| Module | What it models | Anchored to |
| --- | --- | --- |
| `scenarios.py` | Normal / warning / critical ground movement (Gaussian subsidence bowl), blast bursts, sensor drift, missing samples, invalid ToF, dead nodes, battery leak; every sample labelled | Simulation guide stage 1, DGMS thresholds |
| `components/mpu6050.py` | Noise density, offsets, temperature coefficient, bias wander | MPU-6000/6050 datasheet PS-MPU-6000A-00 |
| `components/vl53l0x.py` | Photon budget, sigma, ambient light, plate coverage, range status codes | VL53L0X datasheet DocID029104, UM2039 |
| `components/power.py` | 18650 OCV curve and DW01 cut-off, panel I–V, TP4056 CC/CV with optional NTC, hourly weather | NASA POWER climatology for Jharia (`data/hwsim/`) |
| `components/esp32.py` | Per-state currents, duty cycles, LoRa airtime, RTC drift | ESP32 datasheet v4.x, SX1276 datasheet |
| `node.py` | Firmware loop: sampling, filtering, baseline, detection, timestamps, packets, battery | `firmware/src/wokwi-node/main.cpp` |
| `network.py` | A: Wi-Fi tree mesh with 802.11 retries; B: LoRaWAN pure ALOHA with capture; C: A + LoRa backhaul (Okumura–Hata) | Checked against ALOHA theory and ns-3 |
| `gateway.py` | Pi gateway: last-seen, offline/online, low battery, sensor fault, alert levels, clearing | `services/gateway` rules |
| `e2e.py` | One full run of nodes → network → gateway, with ground-truth scoring | — |
| `experiments.py` | E1–E6 in the report; writes `reports/hwsim/*.json` and `docs/hardware-sim/img/*.png` | — |
| `wokwi_bridge.py` | Feeds a Wokwi serial log to the same gateway logic | — |
| `mqtt_link.py` | Replays a run as MQTT nodes and runs an MQTT gateway for the live dashboard | — |

## Run

```bash
pip install -r services/hw-sim/requirements.txt       # plus gcc or clang on PATH
export PYTHONPATH=services/hw-sim/src

python -m achal_hwsim.e2e --scenario critical --arch wifi_mesh --out data/hwsim/runs/my-run
python -m achal_hwsim.experiments --only e1 e2 e3 e4 e6   # full study, ~20 min; --quick for a smoke run
streamlit run services/hw-sim/dashboard/app.py           # replay any run in data/hwsim/runs
pytest -q services/hw-sim
```

The guide's first experiment is the default `e2e` run: 20 nodes, 10 m spacing, 1 s reporting,
30 minutes, with a lost-packet fade, an offline node, a gateway outage and a low battery injected.
Three ready runs (`critical-{wifi_mesh,lorawan,hybrid}-seed1`) are committed for the dashboard.

Live mode, end to end over MQTT:

```bash
amqtt &                                                   # or mosquitto
python -m achal_hwsim.mqtt_link gateway --run data/hwsim/runs/critical-hybrid-seed1 --state live.json &
python -m achal_hwsim.mqtt_link nodes   --run data/hwsim/runs/critical-hybrid-seed1 --speed 20 &
streamlit run services/hw-sim/dashboard/app.py -- --live live.json
```

ns-3 cross-check (E5): build `ns3/achal-wifi-star.cc` against the `ns3` wheel and point
`ACHAL_NS3_BIN` at it.

```bash
NS=$(python -c "import ns3; print(list(ns3.__path__)[0])")   # the wheel's include/ and lib64/
g++ -std=c++20 -O1 -I$NS/include services/hw-sim/ns3/achal-wifi-star.cc -o achal-wifi-star \
    -L$NS/lib64 -Wl,-rpath,$NS/lib64 $(for l in core network mobility propagation wifi internet applications flow-monitor; do echo -lns3.44-$l; done)
ACHAL_NS3_BIN=$PWD/achal-wifi-star python -m achal_hwsim.experiments --only e5
```

The single-node firmware runs in Wokwi: see [`firmware/wokwi/README.md`](../../firmware/wokwi/README.md).

## Honesty notes

Model parameters in `components/` and `network.py` that are not from a datasheet or a published
measurement are marked `ASSUMPTION` in the source; the report lists them.
This is a research prototype simulation with synthetic data. It is not a certified warning system,
and its thresholds are illustrative until calibrated on site.
