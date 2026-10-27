# Node wiring (reference)

ESP32-WROOM-32 dev board (prototype) or bare module (production PCB). Pins match
`firmware/src/node-esp32/board.h`.

| From | To | Note |
| --- | --- | --- |
| Ra-01H SCK / MISO / MOSI / NSS | GPIO 18 / 19 / 23 / 5 | VSPI |
| Ra-01H RST / DIO0 / DIO1 | GPIO 14 / 26 / 33 | DIO0 = packet received, DIO1 = CAD |
| Ra-01H 3V3 / GND | always-on 3.3 V rail | Antenna via IPEX → RP-SMA bulkhead |
| MPU6050 SDA / SCL | GPIO 21 / 22 | 4.7 k pull-ups on the I2C bus |
| MPU6050 INT | GPIO 34 | Motion wake (ext0), active high |
| MPU6050 VCC | always-on 3.3 V rail | Stays in 20 µA cycle mode during deep sleep |
| ADS1115 SDA / SCL | GPIO 21 / 22 | Address 0x48 (ADDR → GND) |
| ADS1115 VDD | switched sensor rail | |
| Sensor rail | AO3401 P-MOSFET drain | Gate ← GPIO 25 through an inverter or NPN (high = rail on); source ← 3.3 V |
| Crack gauge pot (10 k) | A0 wiper, ends to sensor rail and GND | Ratiometric to A3 |
| Extensometer pot (10 k) | A1 wiper | Spring-returned slider, stainless wire to the next node |
| NTC 10 k B3950 | A2, with 10 k fixed resistor to the sensor rail | NTC to GND; probe buried 0.5 m |
| Sensor rail | A3 | Reference for ratiometric reads |
| VL53L0X (GY-VL53L0XV2) SDA / SCL | GPIO 21 / 22 | Address 0x29, shares the I2C bus |
| VL53L0X VIN | switched sensor rail | Module has its own 2.8 V regulator |
| VL53L0X XSHUT | GPIO 32 | Held low before sleep; high = sensor on |
| Battery + | 100 k / 100 k divider → GPIO 35 | ADC1, works while Wi-Fi is off |
| Siren role: GPIO 27 | relay module IN | Relay switches the 12 V siren |

Power: 2 W solar panel → TP4056 (with DW01 protection) → 18650 cell → HT7333 LDO (4 µA Iq) →
3.3 V rail. Wire a 10 k NTC taped to the cell into the TP4056 TEMP pin (the common module ties it to
GND): the year-long simulation shows 70–440 h a year of charging outside 0–50 °C in a sun-heated box
without it, and none with it (`docs/hardware-sim/report.md`, E3). The HT7333 (250 mA) is sized for
LoRa; a Wi-Fi build would need a ≥500 mA LDO for its ~280 mA transmit peaks. On the dev board, feed 3.3 V into the 3V3 pin (bypassing the on-board AMS1117) and
remove the power LED; both otherwise draw milliamps in sleep.

Mechanical: the enclosure sits on a 1 m GI pipe driven 0.6 m into the ground, so the tilt sensor
follows the ground, not the box. The crack gauge spans a mapped crack or a building wall joint; the
extensometer wire runs in PVC conduit to the neighbouring node, 22.5 m away in the demo grid.

ToF displacement gauge: the VL53L0X looks down a 75 mm PVC sight tube at a 250 × 250 mm white target
plate on a stake driven into the ground 0.5 m away (range ≤ 0.8 m). It measures settlement of the
ground under the plate relative to the node's pipe. It is a short-baseline gauge, not a
node-to-node one: the sensor reaches about 1.2 m on white in default mode and nothing at mesh spacing.
A plate smaller than the sensor's 25° cone biases readings (E2: +114 mm at 80 mm, +24 mm at 150 mm,
none at 250 mm). Bury the MPU6050's pipe 0.6 m and shade the enclosure: un-compensated thermal
drift alone exceeds the 0.1° warning threshold on 95% of simulated units over an April day.
