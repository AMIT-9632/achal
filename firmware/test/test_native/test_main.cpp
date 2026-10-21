// SPDX-License-Identifier: MIT
// Host tests for the frame codec and mesh routing: `pio test -e native`.
#include <string.h>
#include <unity.h>

#include "achal_frame.h"
#include "achal_mesh.h"

using namespace achal;

// Shared with firmware/tools/tests/test_achal_frame.py: both codecs must produce these bytes.
static const char *kGoldenTelemetryHex =
    "ac1290070c07430001120000002c5ed76a84fc0e01f4014b00c8ffc4097e09f60e2102000002d50cb0bab7";

static achal_frame_t golden_frame() {
    achal_frame_t f{};
    f.type = ACHAL_TELEMETRY;
    f.site = achal_site_hash("jharia-demo");
    f.sender = 7;
    f.dest = 12;
    achal_telemetry_t &t = f.u.tel;
    t.origin = 7;
    t.seq = 67;
    t.hop_count = 1;
    t.path[0] = 18;
    t.sample_epoch = 1792499244;  // 2026-10-20T12:27:24Z
    t.tilt_x_mdeg = -892;
    t.tilt_y_mdeg = 270;
    t.vib_rms_100ug = 500;
    t.vib_peak_mg = 75;
    t.crack_10um = -56;
    t.ext_10um = 2500;
    t.temp_cc = 2430;
    t.batt_mv = 3830;
    t.flags = ACHAL_F_TILT_SENSOR | ACHAL_F_MOTION_WAKE;
    t.n_nbr = 2;
    t.nbr[0] = {2, -43};
    t.nbr[1] = {12, -80};
    return f;
}

void setUp() {}
void tearDown() {}

static void to_hex(const uint8_t *b, size_t n, char *out) {
    for (size_t i = 0; i < n; i++) sprintf(out + 2 * i, "%02x", b[i]);
}

void test_crc_check_value() {
    // CRC-16/CCITT-FALSE check value for "123456789".
    TEST_ASSERT_EQUAL_HEX16(0x29B1, achal_crc16((const uint8_t *)"123456789", 9));
}

void test_telemetry_golden_and_roundtrip() {
    achal_frame_t f = golden_frame();
    uint8_t buf[ACHAL_MAX_FRAME];
    size_t n = achal_encode(&f, buf, sizeof(buf));
    TEST_ASSERT_EQUAL(39 + 2 * 2, n);
    char hex[2 * ACHAL_MAX_FRAME + 1] = {};
    to_hex(buf, n, hex);
    TEST_ASSERT_EQUAL_STRING(kGoldenTelemetryHex, hex);

    achal_frame_t d;
    TEST_ASSERT_EQUAL(ACHAL_OK, achal_decode(buf, n, &d));
    TEST_ASSERT_EQUAL(ACHAL_TELEMETRY, d.type);
    TEST_ASSERT_EQUAL(7, d.u.tel.origin);
    TEST_ASSERT_EQUAL(-892, d.u.tel.tilt_x_mdeg);
    TEST_ASSERT_EQUAL(-56, d.u.tel.crack_10um);
    TEST_ASSERT_EQUAL(-80, d.u.tel.nbr[1].rssi_dbm);
    TEST_ASSERT_EQUAL(1792499244u, d.u.tel.sample_epoch);
}

void test_beacon_and_ack_roundtrip() {
    achal_frame_t f{};
    f.type = ACHAL_BEACON;
    f.dest = ACHAL_BROADCAST;
    f.u.beacon = {1792499220, 60, 513, 2, 0x03, 3, 712};
    uint8_t buf[ACHAL_MAX_FRAME];
    size_t n = achal_encode(&f, buf, sizeof(buf));
    TEST_ASSERT_EQUAL(20, n);
    achal_frame_t d;
    TEST_ASSERT_EQUAL(ACHAL_OK, achal_decode(buf, n, &d));
    TEST_ASSERT_EQUAL(513, d.u.beacon.bseq);
    TEST_ASSERT_EQUAL(712, d.u.beacon.tx_offset_ms);
    TEST_ASSERT_EQUAL_HEX8(0x03, d.u.beacon.siren_mask);

    f.type = ACHAL_ACK;
    f.u.ack = {9, 65535};
    n = achal_encode(&f, buf, sizeof(buf));
    TEST_ASSERT_EQUAL(10, n);
    TEST_ASSERT_EQUAL(ACHAL_OK, achal_decode(buf, n, &d));
    TEST_ASSERT_EQUAL(65535, d.u.ack.seq);
}

void test_decode_rejects_corruption() {
    achal_frame_t f = golden_frame();
    uint8_t buf[ACHAL_MAX_FRAME];
    size_t n = achal_encode(&f, buf, sizeof(buf));
    achal_frame_t d;
    buf[20] ^= 0x01;
    TEST_ASSERT_EQUAL(ACHAL_E_CRC, achal_decode(buf, n, &d));
    buf[20] ^= 0x01;
    TEST_ASSERT_EQUAL(ACHAL_E_SHORT, achal_decode(buf, 4, &d));
    buf[0] = 0x00;
    TEST_ASSERT_EQUAL(ACHAL_E_MAGIC, achal_decode(buf, n, &d));
    f.u.tel.n_nbr = ACHAL_MAX_NBR + 1;
    TEST_ASSERT_EQUAL(0, achal_encode(&f, buf, sizeof(buf)));
}

void test_dup_cache() {
    DupCache c;
    TEST_ASSERT_FALSE(c.seen_or_add(3, 10));
    TEST_ASSERT_TRUE(c.seen_or_add(3, 10));
    TEST_ASSERT_FALSE(c.seen_or_add(3, 11));
    TEST_ASSERT_FALSE(c.seen_or_add(0, 0));  // origin 0, seq 0 must not look like an empty slot
    TEST_ASSERT_TRUE(c.seen_or_add(0, 0));
}

void test_parent_prefers_fewer_hops_then_rssi() {
    Neighbours n;
    n.heard(5, -70, 2, 1);
    n.heard(6, -100, 1, 1);
    n.heard(7, -90, 1, 1);
    TEST_ASSERT_EQUAL(7, n.parent(1));
    TEST_ASSERT_EQUAL(2, n.my_hops(1));
    n.heard(0, -110, 0, 1);  // gateway itself
    TEST_ASSERT_EQUAL(0, n.parent(1));
    TEST_ASSERT_EQUAL(1, n.my_hops(1));
}

void test_route_repair_after_link_failures() {
    Neighbours n;
    n.heard(6, -100, 1, 1);
    n.heard(7, -90, 1, 1);
    TEST_ASSERT_EQUAL(7, n.parent(1));
    TEST_ASSERT_EQUAL(6, n.parent(1, 7));  // retry path excludes the parent that failed
    for (uint8_t i = 0; i < kLinkFailLimit; i++) n.link_failed(7);
    TEST_ASSERT_EQUAL(6, n.parent(1));
    n.link_ok(7);
    TEST_ASSERT_EQUAL(7, n.parent(1));
}

void test_stale_neighbours_are_dropped() {
    Neighbours n;
    n.heard(7, -90, 1, 1);
    TEST_ASSERT_EQUAL(7, n.parent(1 + kStaleBeacons - 1));
    TEST_ASSERT_EQUAL(kNoRoute, n.parent(1 + kStaleBeacons));
}

void test_strongest_neighbours_sorted_without_gateway() {
    Neighbours n;
    n.heard(0, -60, 0, 1);
    n.heard(3, -95, 1, 1);
    n.heard(4, -70, 1, 1);
    n.heard(5, -80, kNoRoute, 1);
    achal_nbr_t out[ACHAL_MAX_NBR];
    TEST_ASSERT_EQUAL(3, n.strongest(out, ACHAL_MAX_NBR));
    TEST_ASSERT_EQUAL(4, out[0].id);
    TEST_ASSERT_EQUAL(5, out[1].id);
    TEST_ASSERT_EQUAL(3, out[2].id);
}

void test_slots_and_airtime() {
    TEST_ASSERT_EQUAL(kBeaconPhaseMs, slot_offset_ms(1));
    TEST_ASSERT_EQUAL(kBeaconPhaseMs + 24 * kSlotMs, slot_offset_ms(25));
    TEST_ASSERT_TRUE(window_end_ms(25) > slot_offset_ms(25) + kSlotMs);
    // Semtech LoRa calculator: 51 B, SF7, 125 kHz, CR 4/5, 8-symbol preamble -> 102.7 ms.
    TEST_ASSERT_UINT32_WITHIN(1, 103, lora_airtime_ms(51, 7, 125000, 5, 8));
    // SF12 turns on low data rate optimisation: 51 B -> 2465.8 ms.
    TEST_ASSERT_UINT32_WITHIN(1, 2466, lora_airtime_ms(51, 12, 125000, 5, 8));
    TEST_ASSERT_TRUE(lora_airtime_ms(51, 7, 125000, 5, 8) * 2 < kSlotMs);
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_crc_check_value);
    RUN_TEST(test_telemetry_golden_and_roundtrip);
    RUN_TEST(test_beacon_and_ack_roundtrip);
    RUN_TEST(test_decode_rejects_corruption);
    RUN_TEST(test_dup_cache);
    RUN_TEST(test_parent_prefers_fewer_hops_then_rssi);
    RUN_TEST(test_route_repair_after_link_failures);
    RUN_TEST(test_stale_neighbours_are_dropped);
    RUN_TEST(test_strongest_neighbours_sorted_without_gateway);
    RUN_TEST(test_slots_and_airtime);
    return UNITY_END();
}
