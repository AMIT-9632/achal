// SPDX-License-Identifier: MIT
// ACHAL LoRa gateway radio: an ESP32 + SX1276 on USB serial to the Raspberry Pi.
//
// Radio side: floods the period beacon (time, routing gradient, siren states),
// acks and de-duplicates telemetry frames that reach it over the mesh.
// Serial side (115200 baud, one line each):
//   out  {"v":1,"site":...}      one telemetry.v1 JSON object per reading (no rx_ts: the Pi stamps it)
//   out  # ...                   log lines, ignored by the bridge
//   in   T <unix_seconds>        set the clock (the bridge sends it every minute)
//   in   S <mask> <level>        siren bits (1 = siren-01) and level 0-3, sent in the next beacon
//   in   P <period_s>            sample period for all nodes
// firmware/tools/uart_bridge.py connects this to services/gateway over UDP.
#include <Arduino.h>
#include <sys/time.h>
#include <time.h>

#include "achal_frame.h"
#include "achal_mesh.h"
#include "achal_radio.h"
#include "../node-esp32/board.h"

using namespace achal;

namespace {

uint8_t site_hash;
bool clock_set = false;
uint16_t period_s = 60;
uint16_t bseq = 0;
uint8_t siren_mask = 0, siren_level = 0;
uint64_t next_beacon_ms = 0;
uint32_t n_frames = 0, n_dups = 0, n_bad = 0;
DupCache dup;
String line;

uint64_t now_ms() {
    timeval tv;
    gettimeofday(&tv, nullptr);
    return (uint64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

void schedule_next_beacon() {
    uint64_t p = (uint64_t)period_s * 1000;
    next_beacon_ms = (now_ms() / p + 1) * p;  // period boundaries are aligned to wall-clock time
}

void handle_command(const String &cmd) {
    if (cmd.startsWith("T ")) {
        timeval tv{(time_t)cmd.substring(2).toInt(), 0};
        settimeofday(&tv, nullptr);
        clock_set = true;
        schedule_next_beacon();
        Serial.printf("# clock set %ld\n", (long)tv.tv_sec);
    } else if (cmd.startsWith("S ")) {
        int sp = cmd.indexOf(' ', 2);
        siren_mask = (uint8_t)cmd.substring(2, sp).toInt();
        siren_level = (uint8_t)constrain(cmd.substring(sp + 1).toInt(), 0, 3);
        Serial.printf("# sirens mask=0x%02x level=%u\n", siren_mask, siren_level);
    } else if (cmd.startsWith("P ")) {
        period_s = (uint16_t)constrain(cmd.substring(2).toInt(), 10, 3600);
        schedule_next_beacon();
        Serial.printf("# period %u s\n", period_s);
    }
}

void send_beacon() {
    uint64_t start = next_beacon_ms;
    achal_frame_t f{};
    f.type = ACHAL_BEACON;
    f.site = site_hash;
    f.sender = ACHAL_GATEWAY_ID;
    f.dest = ACHAL_BROADCAST;
    f.u.beacon = {(uint32_t)(start / 1000), period_s, ++bseq, 0, siren_mask, siren_level, 0};
    f.u.beacon.tx_offset_ms = (uint16_t)(now_ms() - start);
    radio::send(f);
    Serial.printf("# beacon %u frames=%lu dups=%lu bad=%lu\n", bseq, (unsigned long)n_frames,
                  (unsigned long)n_dups, (unsigned long)n_bad);
}

void print_node(uint8_t id) { Serial.printf("\"n%02u\"", id); }

void print_telemetry(const achal_telemetry_t &t, float rssi, float snr) {
    char ts[24];
    time_t s = t.sample_epoch;
    tm g;
    gmtime_r(&s, &g);
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &g);

    Serial.printf("{\"v\":1,\"site\":\"%s\",\"node\":\"n%02u\",\"seq\":%u,\"ts\":\"%s\",\"hops\":%u,\"path\":[",
                  SITE_ID, t.origin, t.seq, ts, t.hop_count + 1);
    print_node(t.origin);  // path lists the source, then each relay, as in the simulator
    for (uint8_t i = 0; i < t.hop_count && i < ACHAL_MAX_PATH; i++) {
        Serial.print(',');
        print_node(t.path[i]);
    }
    Serial.printf("],\"rssi_dbm\":%.1f,\"snr_db\":%.1f,\"neighbour_rssi\":{", rssi, snr);
    for (uint8_t i = 0; i < t.n_nbr; i++) {
        if (i) Serial.print(',');
        print_node(t.nbr[i].id);
        Serial.printf(":%d", t.nbr[i].rssi_dbm);
    }
    Serial.printf("},\"tilt_x_deg\":%.3f,\"tilt_y_deg\":%.3f,\"vib_rms_g\":%.4f,\"vib_peak_g\":%.3f,"
                  "\"crack_mm\":%.2f,\"ext_mm\":%.2f,\"temp_c\":%.2f,\"batt_v\":%.3f}\n",
                  t.tilt_x_mdeg / 1000.0, t.tilt_y_mdeg / 1000.0, t.vib_rms_100ug / 10000.0, t.vib_peak_mg / 1000.0,
                  t.crack_10um / 100.0, t.ext_10um / 100.0, t.temp_cc / 100.0, t.batt_mv / 1000.0);
    // telemetry.v1 has no field for status flags yet (proposed for v2), so they go on a log line.
    if (t.flags) Serial.printf("# flags n%02u seq=%u 0x%02x\n", t.origin, t.seq, t.flags);
}

}  // namespace

void setup() {
    Serial.begin(115200);
    site_hash = achal_site_hash(SITE_ID);
    RadioPins pins{PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_NSS, PIN_LORA_RST, PIN_LORA_DIO0,
                   PIN_LORA_DIO1};
    RadioParams params{LORA_FREQ_MHZ, LORA_BW_KHZ, LORA_SF, LORA_CR, LORA_SYNC, LORA_DBM, LORA_PREAMBLE};
    if (!radio::begin(pins, params)) {
        Serial.println("# radio init failed");
    }
    Serial.printf("# achal gateway site=%s hash=0x%02x, waiting for T <unix_seconds>\n", SITE_ID, site_hash);
}

void loop() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n') {
            line.trim();
            handle_command(line);
            line = "";
        } else if (line.length() < 64) {
            line += c;
        }
    }

    if (clock_set && now_ms() >= next_beacon_ms) {
        send_beacon();
        next_beacon_ms += (uint64_t)period_s * 1000;
    }

    RxFrame rx;
    if (!radio::poll(rx, site_hash)) return;
    if (rx.f.type != ACHAL_TELEMETRY || rx.f.dest != ACHAL_GATEWAY_ID) return;
    const achal_telemetry_t &t = rx.f.u.tel;
    achal_frame_t ack{};
    ack.type = ACHAL_ACK;
    ack.site = site_hash;
    ack.sender = ACHAL_GATEWAY_ID;
    ack.dest = rx.f.sender;
    ack.u.ack = {t.origin, t.seq};
    delay(15);
    radio::send(ack);
    if (dup.seen_or_add(t.origin, t.seq)) {
        n_dups++;
        return;
    }
    n_frames++;
    print_telemetry(t, rx.rssi_dbm, rx.snr_db);
}
