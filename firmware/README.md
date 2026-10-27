# ACHAL reference firmware

Firmware for the real hardware that replaces `services/field-sim` in later rounds. It is a
**reference design**: it compiles and its protocol logic is unit-tested on the host, but it has not
yet been flashed to boards or field-tested. The simulator and this firmware share the same
contracts, so moving from simulation to hardware changes nothing downstream of the gateway.

```
 surface nodes ──LoRa IN865 mesh──▶ gateway radio ──USB serial──▶ uart_bridge.py ──UDP──▶ services/gateway
 (src/node-esp32)                   (src/gateway-lora)            (tools/)                 (same input as field-sim)
 siren posts ◀──── beacon carries siren on/off ◀──── "S mask level" ◀──── MQTT achal/{site}/siren/+/cmd
```

| Path | What it is |
| --- | --- |
| `src/node-esp32/` | Surface node: ESP32 + SX1276 + MPU6050 + ADS1115 + VL53L0X. Also builds as a siren controller (`-DROLE_SIREN`) |
| `src/wokwi-node/` | The node's sensing and detection loop for the Wokwi simulator (no radio); see `wokwi/README.md` |
| `lib/achal_dsp/` | Tilt, PPV, dominant frequency, ToF filtering and the alert detector (C, no Arduino). The Python hardware simulator `services/hw-sim` runs this same file |
| `src/gateway-lora/` | Gateway radio: ESP32 + SX1276 on USB serial to the Raspberry Pi |
| `lib/achal_frame/` | Over-the-air frame codec (C, no Arduino), CRC-16 |
| `lib/achal_mesh/` | Routing, duplicate suppression, TDMA timing, LoRa airtime (C++, no Arduino) |
| `lib/achal_radio/` | Thin wrapper over RadioLib's SX1276 driver: listen-before-talk, interrupt receive |
| `tools/achal_frame.py` | Python mirror of the codec, cross-checked against the C code with a shared golden frame |
| `tools/uart_bridge.py` | Pi-side bridge: serial → UDP to `services/gateway`, MQTT siren commands → radio, clock sync |
| `test/test_native/` | Host tests for codec and mesh (`pio test -e native`) |
| `test/test_dsp/` | Host tests for `achal_dsp` |
| `wokwi/` | Wokwi diagram, config and an automated `wokwi-cli` scenario |

Wiring, BOM and power budget: [`../hardware/`](../hardware).

## Build

```bash
pip install platformio
cd firmware
pio test -e native                       # codec + routing tests on your PC
pio run -e node -e siren -e gateway      # build all three images
PLATFORMIO_BUILD_FLAGS="-DNODE_ID=7" pio run -e node -t upload   # flash node n07
```

Each node needs a unique `NODE_ID` (1–99, matching `nNN` in the site config). Siren controllers use
`NODE_ID` 91+ and `SIREN_INDEX` = position in the site's `sirens:` list (1-based). `SITE_ID` and
`N_NODES` are set in `platformio.ini`.

On the Pi: `pip install pyserial -e libs/achal-common && python firmware/tools/uart_bridge.py --port /dev/ttyUSB0`,
then run `services/gateway` as usual. With hardware attached you do not run `field-sim`.

## How the mesh works

LoRa radio is half-duplex and power-hungry when listening, so the mesh is **time-slotted** and the
gateway is the clock.

1. **Beacon flood.** At the start of every period (60 s by default) the gateway sends a 20-byte
   beacon: time, period, sequence number, its hop count (0) and the on/off state of every siren.
   Each node rebroadcasts the first copy it hears once, after a delay that grows with its hop
   count, with its own hop count. `tx_offset_ms` in the beacon lets every node set its clock to
   the gateway's even when it heard a relayed copy.
2. **Routing gradient.** From the beacons it hears, each node keeps a neighbour table (RSSI,
   advertised hops, link failures) and picks as parent the neighbour with the fewest hops to the
   gateway, ties broken by signal strength. Weak links (< −118 dBm) are used only as a last resort.
   Frames never travel more than `max_hops` = 4.
3. **TDMA data window.** Node *k* sends its reading to its parent in slot *k* (250 ms each, after
   the 2 s beacon phase). A parent forwards a child's frame straight away with listen-before-talk.
4. **Acknowledgement and route repair.** The sender overhears its parent forwarding the frame
   (implicit ack) or gets an explicit ACK from the gateway. With no ack, the link is marked failed
   and the frame is retried once in a second pass through the next-best neighbour. Two failures
   take a parent out of use until it works again, and neighbours unheard for 3 beacons are
   forgotten, so the mesh heals around dead or moved nodes on its own.
5. **Sleep.** Leaf nodes light-sleep until their slot and deep-sleep after their ack. Nodes that
   relayed a frame in the last 3 periods stay awake for the whole window. Between periods the
   MPU6050 watches for vibration in its 20 µA cycle mode and wakes the ESP32 on a burst; the peak
   is reported in the next frame with the `motion_wake` flag.
6. **Sirens.** Siren posts are mesh members that read their bit in every beacon and hold the relay
   state through deep sleep. The gateway sets the bits locally, so a siren sounds with no internet.

Every frame a node hears also updates the RSSI it reports in `neighbour_rssi`: a cheap, sensor-free
signal of relative movement between neighbouring nodes that the risk engine can use.

## Over-the-air frame (v1)

Little-endian, CRC-16/CCITT-FALSE. Full layout in `lib/achal_frame/achal_frame.h`.

| Frame | Bytes | Content |
| --- | --- | --- |
| Beacon | 20 | epoch, period, beacon seq, hops, siren mask + level, tx offset |
| Telemetry | 39 + 2 per neighbour (max 51) | origin, seq, relay path (4), sample time, tilt x/y (m°), vibration RMS/peak, crack and extensometer (0.01 mm), temperature, battery mV, fault flags, up to 6 neighbour RSSIs |
| Ack | 10 | origin, seq |

Airtime at SF7 / 125 kHz / CR 4/5: 57 ms for a beacon, 103 ms for a full telemetry frame, so a
250 ms slot fits a frame plus one forward. For long rural links switch to SF9 and widen `kSlotMs`.

The gateway radio prints each telemetry frame as one `telemetry.v1` JSON line (sources and relays
in `path`, `hops` = radio hops, as in the simulator). Status flags have no field in `telemetry.v1`
yet, so they are printed on a `# flags` log line; proposed for `telemetry.v2`.

## What is tested, and what is not

- Host tests (`pio test -e native`, 10 cases): CRC check value, frame round trips, corruption and
  length errors, duplicate cache, parent choice, route repair, stale-neighbour expiry, neighbour
  ranking, slot timing, LoRa airtime against the Semtech formula.
- Python tests (`pytest firmware/tools`): the Python codec produces the same bytes as the C codec
  for a shared golden frame, a decoded frame validates as `telemetry.v1`, siren command folding,
  bridge line routing.
- CI (`.github/workflows/firmware.yml`) builds the node, siren and gateway images with PlatformIO.
- **Not yet done:** flashing real boards, range tests over Jharia terrain, current measurement
  (the power budget is calculated from datasheets), clock-drift tuning of the guard time.

## Licences

All code here is MIT and written for ACHAL. It links RadioLib (MIT) and the Arduino-ESP32 core
(LGPL-2.1, used unmodified as a library). Sensor drivers are written from the MPU-6050 and ADS1115
register maps, so there are no third-party sensor libraries. See `docs/ip-register.md`.
