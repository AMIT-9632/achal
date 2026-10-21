// SPDX-License-Identifier: MIT
// ACHAL surface node. One wake per period:
//   sample sensors -> hear the beacon flood (sync, route, sirens) -> relay it
//   -> send own reading in its TDMA slot -> relay children's frames -> deep sleep.
// A vibration burst between periods wakes the node through the MPU6050
// interrupt; the peak is kept in RTC memory and reported in the next frame.
// Built with -DROLE_SIREN it becomes a siren controller on the same mesh.
#include <Arduino.h>
#include <esp_sleep.h>
#include <string.h>
#include <sys/time.h>

#include "achal_frame.h"
#include "achal_mesh.h"
#include "achal_radio.h"
#include "board.h"
#ifndef ROLE_SIREN
#include "sensors.h"
#endif

using namespace achal;

namespace {

constexpr uint32_t kStateMagic = 0xAC4A0001;
constexpr uint32_t kSampleLeadMs = 2600;  // sensors are read before the radio window opens
constexpr uint8_t kSilentBeaconsToResync = 3;

// Everything that must survive deep sleep. Copied in and out of RTC memory as bytes.
struct State {
    uint32_t magic;
    uint16_t seq;
    uint16_t bseq;
    uint16_t period_s;
    uint64_t period_start_ms;   // gateway time of the last period start we know
    uint8_t missed_beacons;
    uint16_t child_bseq;        // last beacon round in which we relayed a child's frame
    float burst_peak_g;         // largest motion-wake burst since the last report
    uint8_t siren_on;
    Neighbours nbrs;
    DupCache dup;
};

RTC_DATA_ATTR uint8_t rtc_blob[sizeof(State)];
State st;
uint8_t site_hash;

uint64_t now_ms() {
    timeval tv;
    gettimeofday(&tv, nullptr);
    return (uint64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

void set_now_ms(uint64_t ms) {
    timeval tv{(time_t)(ms / 1000), (suseconds_t)((ms % 1000) * 1000)};
    settimeofday(&tv, nullptr);
}

bool has_children() { return (uint16_t)(st.bseq - st.child_bseq) < kStaleBeacons; }

achal_frame_t header(achal_type_t type, uint8_t dest) {
    achal_frame_t f{};
    f.type = type;
    f.site = site_hash;
    f.sender = NODE_ID;
    f.dest = dest;
    return f;
}

void apply_siren(const achal_beacon_t &b) {
#ifdef ROLE_SIREN
    st.siren_on = (b.siren_mask >> (SIREN_INDEX - 1)) & 1;
    gpio_hold_dis((gpio_num_t)PIN_SIREN);
    digitalWrite(PIN_SIREN, st.siren_on ? HIGH : LOW);
    gpio_hold_en((gpio_num_t)PIN_SIREN);  // keep the siren state through deep sleep
#else
    (void)b;
#endif
}

// --- one radio window ------------------------------------------------------

struct Window {
    bool synced = false;
    bool beacon_relayed = false;
    uint64_t beacon_relay_at = 0;
    achal_beacon_t beacon{};
    bool own_acked = false;
    uint16_t own_seq = 0;
    uint8_t own_parent = kNoRoute;
};

void relay_telemetry(achal_frame_t f) {
    achal_telemetry_t &t = f.u.tel;
    if (t.hop_count >= ACHAL_MAX_PATH) return;  // beyond max_hops: drop rather than loop
    uint8_t parent = st.nbrs.parent(st.bseq);
    if (parent == kNoRoute) return;
    t.path[t.hop_count++] = NODE_ID;
    f.sender = NODE_ID;
    f.dest = parent;
    delay(random(10, 80));
    radio::send(f);
    st.child_bseq = st.bseq;
}

void handle(const RxFrame &rx, Window &w) {
    const achal_frame_t &f = rx.f;
    int8_t rssi = (int8_t)constrain((int)rx.rssi_dbm, -128, 0);

    if (f.type == ACHAL_BEACON) {
        const achal_beacon_t &b = f.u.beacon;
        st.nbrs.heard(f.sender, rssi, b.hops, b.bseq);
        if (w.synced) return;  // later copies only refresh the neighbour table
        // The sender put its own delay since period start in tx_offset_ms; add our receive latency.
        uint32_t air = lora_airtime_ms(20, LORA_SF, (uint32_t)(LORA_BW_KHZ * 1000), LORA_CR, LORA_PREAMBLE);
        set_now_ms((uint64_t)b.epoch * 1000 + b.tx_offset_ms + air);
        st.period_start_ms = (uint64_t)b.epoch * 1000;
        st.period_s = b.period_s;
        st.bseq = b.bseq;
        st.missed_beacons = 0;
        w.synced = true;
        w.beacon = b;
        apply_siren(b);
        uint8_t hops = st.nbrs.my_hops(st.bseq);
        if (hops != kNoRoute && hops < ACHAL_MAX_PATH) {
            w.beacon_relay_at = st.period_start_ms + beacon_relay_delay_ms(hops, esp_random());
        } else {
            w.beacon_relayed = true;  // we are at the edge of max_hops: do not extend the flood
        }
        return;
    }

    st.nbrs.heard(f.sender, rssi, kNoRoute, st.bseq);

    if (f.type == ACHAL_ACK) {
        if (f.dest == NODE_ID && f.u.ack.origin == NODE_ID && f.u.ack.seq == w.own_seq) w.own_acked = true;
        return;
    }
    if (f.type == ACHAL_TELEMETRY) {
        const achal_telemetry_t &t = f.u.tel;
        // Implicit ack: our parent forwarding our own frame.
        if (t.origin == NODE_ID && t.seq == w.own_seq && f.sender == w.own_parent) w.own_acked = true;
        if (f.dest != NODE_ID) return;
        if (st.dup.seen_or_add(t.origin, t.seq)) {
            // The child missed our forward and retried: tell it explicitly that we have it.
            achal_frame_t ack = header(ACHAL_ACK, f.sender);
            ack.u.ack.origin = t.origin;
            ack.u.ack.seq = t.seq;
            radio::send(ack);
            return;
        }
        relay_telemetry(f);
    }
}

// Listens (and relays) until gateway time `until_ms`, or until `stop` turns true.
void listen_until(uint64_t until_ms, Window &w, const bool *stop = nullptr) {
    RxFrame rx;
    while (now_ms() < until_ms && !(stop && *stop)) {
        if (radio::poll(rx, site_hash)) handle(rx, w);
        if (w.synced && !w.beacon_relayed && now_ms() >= w.beacon_relay_at) {
            achal_frame_t f = header(ACHAL_BEACON, ACHAL_BROADCAST);
            f.u.beacon = w.beacon;
            f.u.beacon.hops = st.nbrs.my_hops(st.bseq);
            f.u.beacon.tx_offset_ms = (uint16_t)(now_ms() - st.period_start_ms);
            radio::send(f);
            w.beacon_relayed = true;
        }
        delay(1);
    }
}

// Light-sleeps the CPU and the radio until `until_ms` when we have no children to listen for.
void doze_until(uint64_t until_ms) {
    uint64_t now = now_ms();
    if (until_ms <= now + 5) return;
    radio::sleep();
    esp_sleep_enable_timer_wakeup((until_ms - now) * 1000ULL);
    esp_light_sleep_start();
    radio::listen();
}

#ifndef ROLE_SIREN
achal_frame_t make_telemetry(const Reading &r, uint8_t parent) {
    achal_frame_t f = header(ACHAL_TELEMETRY, parent);
    achal_telemetry_t &t = f.u.tel;
    t.origin = NODE_ID;
    t.seq = st.seq;
    t.sample_epoch = (uint32_t)((now_ms() - kSampleLeadMs) / 1000);
    t.tilt_x_mdeg = (int16_t)constrain(lroundf(r.tilt_x_deg * 1000), -32768, 32767);
    t.tilt_y_mdeg = (int16_t)constrain(lroundf(r.tilt_y_deg * 1000), -32768, 32767);
    t.vib_rms_100ug = (uint16_t)constrain(lroundf(r.vib_rms_g * 10000), 0, 65535);
    float peak = fmaxf(r.vib_peak_g, st.burst_peak_g);
    t.vib_peak_mg = (uint16_t)constrain(lroundf(peak * 1000), 0, 65535);
    t.crack_10um = (int16_t)constrain(lroundf(r.crack_mm * 100), -32768, 32767);
    t.ext_10um = (int16_t)constrain(lroundf(r.ext_mm * 100), -32768, 32767);
    t.temp_cc = (int16_t)constrain(lroundf(r.temp_c * 100), -32768, 32767);
    t.batt_mv = r.batt_mv;
    t.flags = r.flags | (st.burst_peak_g > 0 ? ACHAL_F_MOTION_WAKE : 0) | (st.missed_beacons ? ACHAL_F_NO_SYNC : 0);
    t.n_nbr = st.nbrs.strongest(t.nbr, ACHAL_MAX_NBR);
    return f;
}

// Sends our reading in our slot; on no ack, retries once in the retry pass via another neighbour.
void send_own(const Reading &r, Window &w) {
    const uint32_t retry_pass_ms = (uint32_t)N_NODES * kSlotMs;
    for (int attempt = 0; attempt < 2 && !w.own_acked; attempt++) {
        uint64_t slot = st.period_start_ms + slot_offset_ms(NODE_ID) + attempt * retry_pass_ms;
        if (has_children()) listen_until(slot, w); else doze_until(slot);
        uint8_t parent = st.nbrs.parent(st.bseq, attempt ? w.own_parent : kNoRoute);
        if (parent == kNoRoute) break;
        w.own_parent = parent;
        w.own_seq = st.seq;
        achal_frame_t f = make_telemetry(r, parent);
        st.dup.seen_or_add(NODE_ID, st.seq);
        radio::send(f);
        listen_until(now_ms() + 2 * lora_airtime_ms(51, LORA_SF, 125000, LORA_CR, LORA_PREAMBLE) + 200, w,
                     &w.own_acked);
        if (w.own_acked) st.nbrs.link_ok(parent); else st.nbrs.link_failed(parent);
    }
    st.seq++;
    st.burst_peak_g = 0;
}
#endif

void sleep_until_next_period() {
    uint32_t period_ms = (uint32_t)st.period_s * 1000;
    uint64_t next = st.period_start_ms + period_ms;
    while (next < now_ms() + kSampleLeadMs + kGuardMs + 500) next += period_ms;
#ifndef ROLE_SIREN
    next -= kSampleLeadMs;
#endif
    next -= kGuardMs * (1 + st.missed_beacons);  // widen the guard after missed beacons (clock drift)
    radio::sleep();
    memcpy(rtc_blob, &st, sizeof(st));
    esp_sleep_enable_timer_wakeup((next - now_ms()) * 1000ULL);
#ifndef ROLE_SIREN
    sensors::sleep_arm_motion_wake();
    esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_MPU_INT, 1);
#endif
    esp_deep_sleep_start();
}

}  // namespace

void setup() {
    setCpuFrequencyMhz(80);  // plenty for this job and roughly half the active current of 240 MHz
    Serial.begin(115200);
    site_hash = achal_site_hash(SITE_ID);
    memcpy(&st, rtc_blob, sizeof(st));
    if (st.magic != kStateMagic) {
        st = State();
        st.magic = kStateMagic;
        st.period_s = 60;
    }
#ifdef ROLE_SIREN
    pinMode(PIN_SIREN, OUTPUT);
#else
    if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT0) {
        // A burst woke us between periods: record it and go straight back to sleep.
        st.burst_peak_g = fmaxf(st.burst_peak_g, sensors::burst_peak_g());
        sleep_until_next_period();
    }
    sensors::begin();
    if (!sensors::has_baseline()) {
        delay(10000);  // let the installer step away before zeroing
        sensors::store_baseline(sensors::sample());
    }
    Reading reading = sensors::sample();
#endif

    RadioPins pins{PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_NSS, PIN_LORA_RST, PIN_LORA_DIO0,
                   PIN_LORA_DIO1};
    RadioParams params{LORA_FREQ_MHZ, LORA_BW_KHZ, LORA_SF, LORA_CR, LORA_SYNC, LORA_DBM, LORA_PREAMBLE};
    if (!radio::begin(pins, params)) {
        Serial.println("# radio init failed");
        sleep_until_next_period();
    }

    Window w;
    bool cold = st.period_start_ms == 0 || st.missed_beacons >= kSilentBeaconsToResync;
    uint64_t expected = st.period_start_ms + (uint64_t)st.period_s * 1000;
    while (expected + kBeaconPhaseMs < now_ms()) expected += (uint64_t)st.period_s * 1000;
    // Cold start: stay in receive for up to two periods until a beacon arrives.
    uint64_t beacon_deadline = cold ? now_ms() + 2ULL * st.period_s * 1000 : expected + kBeaconPhaseMs;
    listen_until(beacon_deadline, w, &w.synced);
    if (!w.synced) {
        st.missed_beacons++;
        st.period_start_ms = expected;  // free-run on our own clock and try again next period
        if (cold) sleep_until_next_period();
    }
    listen_until(st.period_start_ms + kBeaconPhaseMs, w);  // finish relaying the beacon flood

#ifndef ROLE_SIREN
    send_own(reading, w);
#endif
    if (has_children()) listen_until(st.period_start_ms + window_end_ms(N_NODES), w);
    sleep_until_next_period();
}

void loop() {}  // never reached: every wake ends in deep sleep
