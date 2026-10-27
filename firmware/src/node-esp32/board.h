// SPDX-License-Identifier: MIT
// Pin map and constants for the ACHAL surface node: ESP32-WROOM-32 DevKit +
// SX1276 module at 865 MHz (Ai-Thinker Ra-01H or HopeRF RFM95W-868) +
// MPU6050 + ADS1115. See hardware/wiring.md for the schematic.
#pragma once

#include <stdint.h>

// SX1276 on the VSPI bus
constexpr int PIN_LORA_SCK = 18;
constexpr int PIN_LORA_MISO = 19;
constexpr int PIN_LORA_MOSI = 23;
constexpr int PIN_LORA_NSS = 5;
constexpr int PIN_LORA_RST = 14;
constexpr int PIN_LORA_DIO0 = 26;
constexpr int PIN_LORA_DIO1 = 33;

// I2C: MPU6050 (0x68), ADS1115 (0x48), VL53L0X (0x29)
constexpr int PIN_SDA = 21;
constexpr int PIN_SCL = 22;
constexpr int PIN_MPU_INT = 34;     // MPU6050 motion interrupt, wakes the ESP32 from deep sleep (ext0)
constexpr int PIN_SENSOR_PWR = 25;  // high = sensor rail on (P-MOSFET high-side switch)
constexpr int PIN_BATT_ADC = 35;    // battery via 100k/100k divider
constexpr int PIN_SIREN = 27;       // siren role only: drives the relay / MOSFET for the 12 V siren
constexpr int PIN_LED = 2;
constexpr int PIN_TOF_XSHUT = 32;   // VL53L0X shutdown: low = hardware standby

// IN865: 865-867 MHz, licence-exempt in India (WPC); 17 dBm is well under the 1 W limit
constexpr float LORA_FREQ_MHZ = 865.4025f;
constexpr float LORA_BW_KHZ = 125.0f;
constexpr uint8_t LORA_SF = 7;   // ~103 ms for a 51 B frame; SF9 for long rural links
constexpr uint8_t LORA_CR = 5;   // 4/5
constexpr uint8_t LORA_SYNC = 0x12;  // private network sync word
constexpr int8_t LORA_DBM = 17;
constexpr uint16_t LORA_PREAMBLE = 8;

// Sensors
constexpr uint8_t MPU_ADDR = 0x68;
constexpr uint8_t ADS_ADDR = 0x48;
constexpr uint16_t VIB_SAMPLES = 400;   // 2 s at 200 Hz
constexpr float CRACK_MM_FULL = 25.0f;  // crack gauge pot travel
constexpr float EXT_MM_FULL = 50.0f;    // extensometer pot travel
constexpr float NTC_R25 = 10000.0f, NTC_BETA = 3950.0f, NTC_RFIX = 10000.0f;
constexpr float BATT_DIVIDER = 2.0f;
constexpr uint16_t LOW_BATT_MV = 3450;
constexpr float TAMPER_TILT_DEG = 20.0f;  // beyond this the node was knocked over, not the ground
constexpr float MOTION_WAKE_G = 0.08f;    // MPU6050 motion threshold that wakes the node
// VL53L0X displacement gauge: ranges to a white plate on a reference stake ~0.5 m away across a
// mapped crack, inside a shaded sight tube (services/hw-sim experiment e2 for why not node to node).
constexpr uint8_t TOF_SHOTS = 7;              // burst, median-filtered by achal_tof_filter
constexpr uint32_t TOF_BUDGET_US = 200000;    // high-accuracy mode
constexpr uint16_t TOF_MAX_RANGE_MM = 800;    // gauge length + margin; longer is a miss, not movement

#ifndef NODE_ID
#define NODE_ID 1
#endif
#ifndef N_NODES
#define N_NODES 25
#endif
#ifndef SITE_ID
#define SITE_ID "jharia-demo"
#endif
// ROLE_SIREN builds the same firmware as a siren controller (no sensors).
// SIREN_INDEX is its bit in the beacon siren_mask (1-based).
#ifndef SIREN_INDEX
#define SIREN_INDEX 1
#endif
