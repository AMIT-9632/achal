// SPDX-License-Identifier: MIT
// ACHAL node for the Wokwi ESP32 simulator (stage 2 of the hardware simulation guide).
//
// Real ESP32 firmware, run instruction by instruction in Wokwi with a simulated
// MPU6050 on I2C. Wokwi has no VL53L0X part, so the displacement input comes
// from a slide potentiometer (0-1000 mm) and a "ToF fault" button; the firmware
// turns them into 7-shot VL53L0X-like bursts (noise, invalid statuses) and runs
// the same median filter it would run on the real sensor.
//
// Every window: 100 Hz accelerometer acquisition, tilt (EMA), vibration features
// (RMS, peak, PPV, dominant frequency), ToF every 5 s, event detection, a
// timestamped JSON packet on the serial port. All processing is
// lib/achal_dsp, the same C code the Python simulator calls.
//
// Sleep/wake: after 30 quiet windows the node drops to ECO mode (one window
// every 10 s, light sleep in between); any vibration peak above 0.05 g, a ToF
// change or an alarm brings it straight back to FAST mode (1 s).
// The red LED and the buzzer are the on-site alarm (critical = steady + buzzer).
#include <Arduino.h>
#include <Wire.h>
#include <esp_sleep.h>
#include <math.h>

#include "achal_dsp.h"

namespace {

constexpr int PIN_SDA = 21, PIN_SCL = 22, PIN_TOF_POT = 34, PIN_TOF_FAULT = 4, PIN_LED = 2, PIN_BUZZER = 27;
constexpr uint8_t MPU = 0x68;
constexpr float FS_HZ = 100.0f;
constexpr uint16_t N_WIN = 100;            // 1 s window
constexpr uint32_t TOF_EVERY_MS = 5000;
constexpr uint8_t BASELINE_WINDOWS = 5;
constexpr uint8_t QUIET_TO_ECO = 30;
constexpr uint32_t ECO_PERIOD_MS = 10000;

float az[N_WIN];
achal_det_cfg_t det_cfg;
achal_det_state_t det;
achal_tof_cfg_t tof_cfg = {3, 15.0f, 0.25f, 1300};
float ema_x = NAN, ema_y = NAN, base_x = 0, base_y = 0, base_tof = NAN;
float tof_hist[3];
uint8_t tof_n = 0, baseline_n = 0, quiet = 0;
float sum_bx = 0, sum_by = 0, sum_tof = 0;
uint8_t sum_tof_n = 0;
uint32_t seq = 0, last_tof_ms = 0;
bool eco = false;
float tof_mm = NAN;
uint8_t tof_valid = 0;

void wr(uint8_t reg, uint8_t v) {
    Wire.beginTransmission(MPU);
    Wire.write(reg);
    Wire.write(v);
    Wire.endTransmission();
}

bool accel(float &x, float &y, float &z, int16_t *temp = nullptr) {
    Wire.beginTransmission(MPU);
    Wire.write(0x3B);
    if (Wire.endTransmission(false) != 0 || Wire.requestFrom(MPU, (uint8_t)8) != 8) return false;
    int16_t r[4];
    for (int i = 0; i < 4; i++) r[i] = (int16_t)(Wire.read() << 8 | Wire.read());
    x = achal_mpu_counts_to_g(r[0], 2);
    y = achal_mpu_counts_to_g(r[1], 2);
    z = achal_mpu_counts_to_g(r[2], 2);
    if (temp) *temp = r[3];
    return true;
}

// Simulated VL53L0X burst: pot gives the true distance; noise grows with distance;
// the fault button makes every shot fail with a phase (out of range) status.
void tof_burst(achal_tof_sample_t *s, uint8_t n) {
    float d = analogRead(PIN_TOF_POT) / 4095.0f * 1000.0f + 200.0f;  // 200..1200 mm
    bool fault = digitalRead(PIN_TOF_FAULT) == LOW;
    for (uint8_t i = 0; i < n; i++) {
        float sigma = 1.0f + 2.0f * (d / 1000.0f) * (d / 1000.0f);
        float noise = ((float)esp_random() / 4294967295.0f - 0.5f) * 3.46f * sigma;  // uniform, same sigma
        s[i].range_mm = fault ? 8190 : (uint16_t)lroundf(d + noise);
        s[i].status = fault ? ACHAL_TOF_PHASE_FAIL : ACHAL_TOF_VALID;
        s[i].signal_mcps = fault ? 0.0f : 0.25f * (1200.0f / d) * (1200.0f / d);
        s[i].sigma_mm = sigma;
    }
}

const char *level_name(uint8_t l) { return l == 2 ? "critical" : l == 1 ? "warning" : "normal"; }

}  // namespace

void setup() {
    Serial.begin(115200);
    Wire.begin(PIN_SDA, PIN_SCL, 400000);
    pinMode(PIN_TOF_FAULT, INPUT_PULLUP);
    pinMode(PIN_LED, OUTPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    wr(0x6B, 0x01);  // wake, PLL
    wr(0x6C, 0x07);  // gyros off
    wr(0x1A, 0x03);  // DLPF 44 Hz
    wr(0x19, 9);     // 1 kHz / 10 = 100 Hz
    wr(0x1C, 0x00);  // +-2 g
    achal_det_default(&det_cfg);
    det_cfg.clear_n = 5;
    achal_det_reset(&det);
    Serial.println("{\"boot\":\"achal-wokwi-node\",\"dsp\":1}");
}

void loop() {
    uint32_t t0 = millis();
    double sx = 0, sy = 0, sz = 0;
    uint16_t n = 0;
    int16_t temp_raw = 0;
    uint32_t next = micros();
    for (uint16_t i = 0; i < N_WIN; i++) {
        while ((int32_t)(micros() - next) < 0) {
        }
        next += (uint32_t)(1e6f / FS_HZ);
        float x, y, z;
        if (!accel(x, y, z, i == 0 ? &temp_raw : nullptr)) break;
        sx += x, sy += y, sz += z;
        az[n++] = z;
    }
    uint8_t flags = 0;
    float tx = 0, ty = 0;
    achal_vib_t vib = {0, 0, 0, 0};
    if (n < N_WIN / 2) {
        flags |= 1;  // incomplete window: not used
    } else {
        float rx, ry;
        achal_tilt_deg(sx / n, sy / n, sz / n, &rx, &ry);
        ema_x = achal_ema(ema_x, rx, 1.0f / 11.0f);
        ema_y = achal_ema(ema_y, ry, 1.0f / 11.0f);
        achal_vib_features(az, n, FS_HZ, &vib);
    }

    if (millis() - last_tof_ms >= TOF_EVERY_MS || last_tof_ms == 0) {
        last_tof_ms = millis();
        achal_tof_sample_t shots[7];
        tof_burst(shots, 7);
        float mm;
        if (achal_tof_filter(&tof_cfg, shots, 7, &mm, &tof_valid)) {
            tof_mm = mm;
            if (!isnan(base_tof)) {
                tof_hist[tof_n % 3] = mm;
                tof_n++;
            }
        } else {
            tof_mm = NAN;
            flags |= 2;
        }
    }

    float disp = NAN;
    uint8_t level = 0;
    if (baseline_n < BASELINE_WINDOWS) {
        if (!(flags & 1)) { sum_bx += ema_x; sum_by += ema_y; baseline_n++; }
        if (!isnan(tof_mm)) { sum_tof += tof_mm; sum_tof_n++; }
        if (baseline_n == BASELINE_WINDOWS) {
            base_x = sum_bx / baseline_n;
            base_y = sum_by / baseline_n;
            base_tof = sum_tof_n ? sum_tof / sum_tof_n : NAN;
        }
    } else {
        tx = ema_x - base_x;
        ty = ema_y - base_y;
        if (tof_n >= 3) disp = achal_median3(tof_hist[0], tof_hist[1], tof_hist[2]) - base_tof;
        level = achal_detect(&det_cfg, &det, hypotf(tx, ty), isnan(disp) ? 0.0f : disp, !isnan(disp),
                             vib.ppv_mm_s, vib.dom_hz);
    }
    if (vib.ppv_mm_s >= achal_dgms_ppv_limit_mm_s(vib.dom_hz)) flags |= 64;

    digitalWrite(PIN_LED, level >= 1 ? HIGH : LOW);
    if (level == 2) tone(PIN_BUZZER, 2000); else noTone(PIN_BUZZER);

    bool active = level > 0 || vib.peak_g > 0.05f || (!isnan(disp) && fabsf(disp) > 2.0f);
    quiet = active ? 0 : (quiet < 255 ? quiet + 1 : 255);
    bool was_eco = eco;
    eco = quiet >= QUIET_TO_ECO;

    Serial.printf("{\"node\":\"w01\",\"seq\":%lu,\"ts_ms\":%lu,\"mode\":\"%s\",\"tilt_x_deg\":%.4f,\"tilt_y_deg\":%.4f,"
                  "\"vib_rms_g\":%.4f,\"vib_peak_g\":%.4f,\"ppv_mm_s\":%.2f,\"dom_hz\":%.1f,\"tof_mm\":%.1f,"
                  "\"tof_valid\":%u,\"disp_mm\":%.1f,\"temp_c\":%.1f,\"level\":%u,\"level_name\":\"%s\",\"cause\":%u,"
                  "\"flags\":%u}\n",
                  (unsigned long)seq++, (unsigned long)t0, eco ? "eco" : "fast", tx, ty, vib.rms_g, vib.peak_g,
                  vib.ppv_mm_s, vib.dom_hz, isnan(tof_mm) ? -1.0f : tof_mm, tof_valid, isnan(disp) ? 0.0f : disp,
                  achal_mpu_temp_c(temp_raw), level, level_name(level), det.cause, flags);
    if (eco != was_eco) Serial.printf("{\"mode_change\":\"%s\"}\n", eco ? "eco" : "fast");

    uint32_t period = eco ? ECO_PERIOD_MS : 1000;
    uint32_t used = millis() - t0;
    if (used < period) {
        Serial.flush();
        if (eco) {
            esp_sleep_enable_timer_wakeup((uint64_t)(period - used) * 1000ULL);
            esp_light_sleep_start();
        } else {
            delay(period - used);
        }
    }
}
