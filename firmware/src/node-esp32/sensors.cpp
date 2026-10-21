// SPDX-License-Identifier: MIT
// Register-level drivers for MPU6050 and ADS1115, so the node has no
// third-party sensor libraries to license or audit.
#include "sensors.h"

#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <math.h>

#include "achal_frame.h"
#include "board.h"

namespace {

// MPU6050 registers (MPU-6000/6050 Register Map rev 4.2)
constexpr uint8_t SMPLRT_DIV = 0x19, CONFIG = 0x1A, ACCEL_CONFIG = 0x1C, MOT_THR = 0x1F, MOT_DUR = 0x20;
constexpr uint8_t INT_PIN_CFG = 0x37, INT_ENABLE = 0x38, INT_STATUS = 0x3A, ACCEL_XOUT_H = 0x3B;
constexpr uint8_t PWR_MGMT_1 = 0x6B, PWR_MGMT_2 = 0x6C, WHO_AM_I = 0x75;
constexpr float ACC_LSB_PER_G = 16384.0f;  // +-2 g range

struct Baseline {
    float tilt_x = 0, tilt_y = 0, crack = 0, ext = 0;
};

Baseline base;
bool base_loaded = false;
bool mpu_ok = false, ads_ok = false;

bool write_reg(uint8_t addr, uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

bool read_regs(uint8_t addr, uint8_t reg, uint8_t *buf, uint8_t n) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(addr, n) != n) return false;
    for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
    return true;
}

bool mpu_accel(float &ax, float &ay, float &az) {
    uint8_t b[6];
    if (!read_regs(MPU_ADDR, ACCEL_XOUT_H, b, 6)) return false;
    ax = (int16_t)(b[0] << 8 | b[1]) / ACC_LSB_PER_G;
    ay = (int16_t)(b[2] << 8 | b[3]) / ACC_LSB_PER_G;
    az = (int16_t)(b[4] << 8 | b[5]) / ACC_LSB_PER_G;
    return true;
}

bool mpu_begin() {
    uint8_t who = 0;
    if (!read_regs(MPU_ADDR, WHO_AM_I, &who, 1) || who != 0x68) return false;
    return write_reg(MPU_ADDR, PWR_MGMT_1, 0x01)       // wake, PLL on gyro X
           && write_reg(MPU_ADDR, PWR_MGMT_2, 0x07)    // gyros off: we only need the accelerometer
           && write_reg(MPU_ADDR, CONFIG, 0x02)        // DLPF 94 Hz
           && write_reg(MPU_ADDR, SMPLRT_DIV, 4)       // 1 kHz / 5 = 200 Hz
           && write_reg(MPU_ADDR, ACCEL_CONFIG, 0x00)  // +-2 g
           && write_reg(MPU_ADDR, INT_ENABLE, 0x00);
}

// ADS1115 single-shot read of AINch vs GND, +-4.096 V range, 128 SPS. Returns volts or NAN.
float ads_read(uint8_t ch) {
    uint16_t cfg = 0x8000 | ((4 + ch) << 12) | (1 << 9) | (1 << 8) | (4 << 5) | 0x03;
    Wire.beginTransmission(ADS_ADDR);
    Wire.write(0x01);
    Wire.write(cfg >> 8);
    Wire.write(cfg & 0xFF);
    if (Wire.endTransmission() != 0) return NAN;
    delay(9);
    uint8_t b[2];
    if (!read_regs(ADS_ADDR, 0x00, b, 2)) return NAN;
    return (int16_t)(b[0] << 8 | b[1]) * 4.096f / 32768.0f;
}

float ads_avg(uint8_t ch, int n = 4) {
    float s = 0;
    for (int i = 0; i < n; i++) {
        float v = ads_read(ch);
        if (isnan(v)) return NAN;
        s += v;
    }
    return s / n;
}

void load_baseline() {
    if (base_loaded) return;
    Preferences p;
    p.begin("achal", true);
    if (p.isKey("base")) p.getBytes("base", &base, sizeof(base));
    p.end();
    base_loaded = true;
}

}  // namespace

namespace sensors {

void begin() {
    pinMode(PIN_SENSOR_PWR, OUTPUT);
    digitalWrite(PIN_SENSOR_PWR, HIGH);
    Wire.begin(PIN_SDA, PIN_SCL, 400000);
    delay(5);
    mpu_ok = mpu_begin();
    ads_ok = !isnan(ads_read(3));
    load_baseline();
}

Reading sample() {
    Reading r;
    if (!mpu_ok) r.flags |= ACHAL_F_TILT_SENSOR;
    if (!ads_ok) r.flags |= ACHAL_F_ADC;

    // Accelerometer: mean vector gives tilt, magnitude deviations give vibration.
    double sx = 0, sy = 0, sz = 0, sm = 0, smm = 0;
    float peak = 0, mag_first = NAN;
    int n = 0;
    uint32_t next = micros();
    for (uint16_t i = 0; mpu_ok && i < VIB_SAMPLES; i++) {
        while ((int32_t)(micros() - next) < 0) {
        }
        next += 5000;
        float ax, ay, az;
        if (!mpu_accel(ax, ay, az)) {
            r.flags |= ACHAL_F_TILT_SENSOR;
            break;
        }
        float m = sqrtf(ax * ax + ay * ay + az * az);
        if (isnan(mag_first)) mag_first = m;
        sx += ax, sy += ay, sz += az, sm += m, smm += (double)m * m;
        peak = fmaxf(peak, fabsf(m - mag_first));
        n++;
    }
    if (n > 0) {
        double mx = sx / n, my = sy / n, mz = sz / n, mean_m = sm / n;
        float tx = atan2f(mx, sqrtf(my * my + mz * mz)) * 57.29578f;
        float ty = atan2f(my, sqrtf(mx * mx + mz * mz)) * 57.29578f;
        r.tilt_x_deg = tx - base.tilt_x;
        r.tilt_y_deg = ty - base.tilt_y;
        r.vib_rms_g = sqrtf(fmaxf(smm / n - mean_m * mean_m, 0.0));
        r.vib_peak_g = fmaxf(peak, fabsf(mag_first - (float)mean_m));
        if (fabsf(r.tilt_x_deg) > TAMPER_TILT_DEG || fabsf(r.tilt_y_deg) > TAMPER_TILT_DEG) r.flags |= ACHAL_F_TAMPER;
    }

    // Potentiometric crack gauge (A0), extensometer (A1), NTC (A2), ratiometric to the rail on A3.
    if (ads_ok) {
        float vref = ads_avg(3);
        float v0 = ads_avg(0), v1 = ads_avg(1), v2 = ads_avg(2);
        if (isnan(vref) || vref < 1.0f || isnan(v0) || isnan(v1) || isnan(v2)) {
            r.flags |= ACHAL_F_ADC;
        } else {
            r.crack_mm = v0 / vref * CRACK_MM_FULL - base.crack;
            r.ext_mm = v1 / vref * EXT_MM_FULL - base.ext;
            float rntc = NTC_RFIX * v2 / fmaxf(vref - v2, 1e-3f);
            r.temp_c = 1.0f / (1.0f / 298.15f + logf(rntc / NTC_R25) / NTC_BETA) - 273.15f;
        }
    }

    uint32_t mv = 0;
    for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(PIN_BATT_ADC);
    r.batt_mv = (uint16_t)(mv / 16 * BATT_DIVIDER);
    if (r.batt_mv < LOW_BATT_MV) r.flags |= ACHAL_F_LOW_BATT;
    return r;
}

bool has_baseline() {
    load_baseline();
    Preferences p;
    p.begin("achal", true);
    bool ok = p.isKey("base");
    p.end();
    return ok;
}

void store_baseline(const Reading &raw_now) {
    // raw_now was taken with a zero baseline, so its values are the absolute install readings.
    base.tilt_x = raw_now.tilt_x_deg;
    base.tilt_y = raw_now.tilt_y_deg;
    base.crack = raw_now.crack_mm;
    base.ext = raw_now.ext_mm;
    Preferences p;
    p.begin("achal", false);
    p.putBytes("base", &base, sizeof(base));
    p.end();
}

void sleep_arm_motion_wake() {
    if (mpu_ok) {
        uint8_t clear;
        read_regs(MPU_ADDR, INT_STATUS, &clear, 1);
        write_reg(MPU_ADDR, ACCEL_CONFIG, 0x01);  // high-pass 5 Hz so gravity does not trigger
        write_reg(MPU_ADDR, MOT_THR, (uint8_t)(MOTION_WAKE_G / 0.002f));
        write_reg(MPU_ADDR, MOT_DUR, 1);
        write_reg(MPU_ADDR, INT_PIN_CFG, 0x20);    // active high, latched until INT_STATUS is read
        write_reg(MPU_ADDR, INT_ENABLE, 0x40);     // motion interrupt
        write_reg(MPU_ADDR, PWR_MGMT_2, 0x47);     // 5 Hz low-power wake-ups, gyros off
        write_reg(MPU_ADDR, PWR_MGMT_1, 0x20);     // cycle mode, ~10 uA
    }
    // The MPU6050 sits on the always-on 3.3 V rail; ADS1115, pots and NTC are switched off.
    digitalWrite(PIN_SENSOR_PWR, LOW);
}

float burst_peak_g() {
    Wire.begin(PIN_SDA, PIN_SCL, 400000);
    if (!mpu_begin()) return 0;
    mpu_ok = true;
    float first = NAN, peak = 0;
    for (int i = 0; i < 100; i++) {  // 0.5 s at 200 Hz
        float ax, ay, az;
        if (!mpu_accel(ax, ay, az)) break;
        float m = sqrtf(ax * ax + ay * ay + az * az);
        if (isnan(first)) first = m;
        peak = fmaxf(peak, fabsf(m - first));
        delay(5);
    }
    return peak;
}

}  // namespace sensors
