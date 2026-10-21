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
| Battery + | 100 k / 100 k divider → GPIO 35 | ADC1, works while Wi-Fi is off |
| Siren role: GPIO 27 | relay module IN | Relay switches the 12 V siren |

Power: 2 W solar panel → TP4056 (with DW01 protection) → 18650 cell → HT7333 LDO (4 µA Iq) →
3.3 V rail. On the dev board, feed 3.3 V into the 3V3 pin (bypassing the on-board AMS1117) and
remove the power LED; both otherwise draw milliamps in sleep.

Mechanical: the enclosure sits on a 1 m GI pipe driven 0.6 m into the ground, so the tilt sensor
follows the ground, not the box. The crack gauge spans a mapped crack or a building wall joint; the
extensometer wire runs in PVC conduit to the neighbouring node, 22.5 m away in the demo grid.
