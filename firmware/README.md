# Reference firmware (not yet flashed)

- `node-esp32/`: PlatformIO project for the surface node (ESP32 + SX1276 + MPU6050), deep sleep between samples.
- `gateway-lora/`: LoRa receiver that forwards frames over UART to the Raspberry Pi gateway.

Written as a reference design this round (W5); frames match `schemas/telemetry.v1.json`.
