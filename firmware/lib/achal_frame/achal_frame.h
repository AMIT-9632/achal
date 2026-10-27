// SPDX-License-Identifier: MIT
// ACHAL over-the-air frame format, v1.
//
// LoRa airtime is the scarce resource, so nodes do not send the telemetry
// JSON of schemas/telemetry.v1.json. They send this compact little-endian
// binary frame instead; the gateway decodes it and prints the JSON line.
// The Python mirror is firmware/tools/achal_frame.py. Keep the two in sync
// and update the golden vector in both test suites on any change.
//
// Common layout (all integers little-endian):
//   0  magic        0xAC
//   1  ver_type     high nibble = version (1), low nibble = frame type
//   2  site         8-bit hash of the site_id (frames from other sites are ignored)
//   3  sender       last transmitter (0 = gateway, 1..99 = node nN)
//   4  dest         intended next hop (0 = gateway, 0xFF = broadcast)
//   5  payload      type specific, see below
//   n  crc16        CRC-16/CCITT-FALSE over bytes 0..n-1
//
// Payload fields are packed in the order of the structs below, with no
// padding. Telemetry carries tof_mm (uint16) right after n_nbr, in the two
// bytes that were reserved (zero) before 2026-10-27, so old frames decode as
// "no ToF reading"; then n_nbr (id, rssi) pairs. Sizes: beacon 20 B, ack 10 B, telemetry 39 + 2*n_nbr B.
#ifndef ACHAL_FRAME_H
#define ACHAL_FRAME_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ACHAL_MAGIC 0xAC
#define ACHAL_VERSION 1
#define ACHAL_GATEWAY_ID 0
#define ACHAL_BROADCAST 0xFF
#define ACHAL_MAX_PATH 4
#define ACHAL_MAX_NBR 6
#define ACHAL_MAX_FRAME 64

typedef enum {
    ACHAL_BEACON = 1,     // gateway -> all, flooded once per period: time sync, routing gradient, sirens
    ACHAL_TELEMETRY = 2,  // node -> gateway, relayed hop by hop along the gradient
    ACHAL_ACK = 3,        // gateway -> last hop: telemetry received (relays ack implicitly by forwarding)
} achal_type_t;

// Fault / status flags carried in telemetry.flags.
enum {
    ACHAL_F_TILT_SENSOR = 1 << 0,  // accelerometer did not answer
    ACHAL_F_ADC = 1 << 1,          // ADS1115 (crack / extensometer / NTC) did not answer
    ACHAL_F_LOW_BATT = 1 << 2,
    ACHAL_F_TAMPER = 1 << 3,       // enclosure tilted beyond physical range
    ACHAL_F_NO_SYNC = 1 << 4,      // missed the last beacon, sample time is estimated
    ACHAL_F_MOTION_WAKE = 1 << 5,  // a vibration burst woke the node since its last report
    ACHAL_F_TOF = 1 << 6,          // VL53L0X gave no valid reading this period (tof_mm = 0)
};

typedef struct {
    uint32_t epoch;       // unix seconds at the start of this period (gateway clock)
    uint16_t period_s;    // sample period all nodes follow
    uint16_t bseq;        // beacon sequence number
    uint8_t hops;         // sender's hop distance to the gateway (gateway = 0)
    uint8_t siren_mask;   // bit i set = siren i+1 on
    uint8_t siren_level;  // 0 normal, 1 watch, 2 warning, 3 critical
    uint16_t tx_offset_ms;  // ms between period start and this (re)transmission, so relayed copies sync too
} achal_beacon_t;

typedef struct {
    uint8_t origin;
    uint16_t seq;
} achal_ack_t;

typedef struct {
    uint8_t id;
    int8_t rssi_dbm;
} achal_nbr_t;

typedef struct {
    uint8_t origin;                   // node that took the reading
    uint16_t seq;
    uint8_t hop_count;                // relays so far
    uint8_t path[ACHAL_MAX_PATH];     // relay ids in order, 0 = unused
    uint32_t sample_epoch;            // unix seconds at sampling (node clock, synced by beacon)
    int16_t tilt_x_mdeg;
    int16_t tilt_y_mdeg;
    uint16_t vib_rms_100ug;           // 1e-4 g
    uint16_t vib_peak_mg;             // 1e-3 g
    int16_t crack_10um;               // 0.01 mm
    int16_t ext_10um;                 // 0.01 mm
    int16_t temp_cc;                  // 0.01 degC
    uint16_t batt_mv;
    uint8_t flags;
    uint8_t n_nbr;
    achal_nbr_t nbr[ACHAL_MAX_NBR];
    uint16_t tof_mm;                  // VL53L0X gauge distance (median of a burst), mm; 0 = no reading
} achal_telemetry_t;

typedef struct {
    achal_type_t type;
    uint8_t site;
    uint8_t sender;
    uint8_t dest;
    union {
        achal_beacon_t beacon;
        achal_telemetry_t tel;
        achal_ack_t ack;
    } u;
} achal_frame_t;

typedef enum {
    ACHAL_OK = 0,
    ACHAL_E_SHORT = -1,
    ACHAL_E_MAGIC = -2,
    ACHAL_E_VERSION = -3,
    ACHAL_E_CRC = -4,
    ACHAL_E_TYPE = -5,
    ACHAL_E_LENGTH = -6,
} achal_status_t;

uint16_t achal_crc16(const uint8_t *buf, size_t len);
uint8_t achal_site_hash(const char *site_id);

// Returns the encoded length, or 0 if cap is too small.
size_t achal_encode(const achal_frame_t *f, uint8_t *buf, size_t cap);
achal_status_t achal_decode(const uint8_t *buf, size_t len, achal_frame_t *out);

#ifdef __cplusplus
}
#endif

#endif
