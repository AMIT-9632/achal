# ACHAL node in Wokwi (simulation guide, stage 2)

The real ESP32 firmware `src/wokwi-node/main.cpp`, built for an ESP32 DevKit and run in the
[Wokwi](https://wokwi.com) simulator with a simulated MPU6050. It exercises acquisition (100 Hz
accelerometer over I2C), filtering (tilt EMA, ToF median and median-of-3), timestamping, event
detection, packet formatting (one JSON line per window) and sleep/wake logic (FAST 1 s / ECO 10 s
with light sleep). All maths is `lib/achal_dsp`, the same C file the Python simulator in
`services/hw-sim` loads, so the two make identical decisions on identical inputs.

| Part | Stands for |
| --- | --- |
| `imu1` MPU6050 | the node's MPU6050. Its accelX/Y/Z controls set ground tilt (0.0175 g ≈ 1°) |
| `tof_pot` slide potentiometer | VL53L0X distance, 200–1200 mm. Wokwi has no VL53L0X part, so the firmware turns the pot into 7-shot bursts with distance-dependent noise |
| `tof_fault` button | held = every ToF shot fails (phase-fail status 4, range 8190) |
| red LED + buzzer | on-site alarm: LED on at warning, buzzer at critical |

## Run

```bash
cd firmware && pio run -e wokwi          # builds .pio/build/wokwi/firmware.{bin,elf}
```

Then either open `firmware/wokwi/diagram.json` with the Wokwi VS Code extension (it reads
`wokwi.toml`), or run headless with the automated scenario:

```bash
export WOKWI_CLI_TOKEN=...               # free token from https://wokwi.com/dashboard/ci
wokwi-cli firmware/wokwi --scenario tilt-alarm.test.yaml --timeout 90000 --serial-log-file wokwi-serial.log
python -m achal_hwsim.wokwi_bridge wokwi-serial.log   # feeds the log to the Pi gateway logic and prints alerts
```

CI runs the same scenario when the repository has a `WOKWI_CLI_TOKEN` secret
(`.github/workflows/firmware.yml`, job `wokwi`).

## Manual test script

1. Start the simulation, wait ~12 s for `"level":0` lines (baseline taken).
2. Click the MPU6050 and set accelX to 0.003 g → after 3 windows `"level_name":"warning"`, LED on.
3. Set accelX to 0.010 g → `"critical"`, buzzer sounds.
4. Set accelX back to 0 → the level steps down after 5 quiet windows.
5. Move the slide potentiometer by ~15 % (~150 mm) → after three ToF readings (15 s) a displacement critical.
6. Hold `ToF fault` → `tof_valid` drops to 0, `flags` bit 2 set, the displacement channel is ignored, not read as zero.
7. Leave it alone for 30 s → `{"mode_change":"eco"}`, one line every 10 s; tap the board's accelZ to 1.1 g → back to fast.

## Limits

Wokwi simulates the ESP32 and the MPU6050's registers; it does not simulate mine physics, sensor
noise of a real MPU6050, the VL53L0X, solar charging or radio links. Those are modelled in
`services/hw-sim` (see `docs/hardware-sim/report.md`).
