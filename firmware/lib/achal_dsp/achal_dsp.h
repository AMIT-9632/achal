// SPDX-License-Identifier: MIT
// ACHAL on-node signal processing and event detection.
//
// Plain C99, no Arduino, no malloc, so the same object code runs on the
// ESP32 node, in the Wokwi node (firmware/src/wokwi-node) and inside the
// Python hardware simulator (services/hw-sim loads it with ctypes). One
// implementation means the simulated node makes exactly the decisions the
// real one would.
//
// Units: acceleration in g, angles in degrees, distance in mm, velocity in
// mm/s, temperature in degC.
#ifndef ACHAL_DSP_H
#define ACHAL_DSP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ACHAL_DSP_VERSION 1

// --- MPU6050 -----------------------------------------------------------------

// Raw 16-bit accelerometer counts to g for the configured full-scale range
// (fs_g = 2, 4, 8 or 16; MPU-6000/6050 datasheet rev 3.4 table 6.2).
float achal_mpu_counts_to_g(int16_t counts, uint8_t fs_g);

// Linear temperature compensation of one accelerometer axis offset:
// a - k * (t - t_ref). k (g/degC) comes from a two-temperature calibration of
// each board, t from the MPU6050's own die-temperature sensor.
float achal_accel_temp_comp(float a_g, float k_g_per_c, float t_c, float t_ref_c);

// Die temperature from the TEMP_OUT register (datasheet: /340 + 36.53).
float achal_mpu_temp_c(int16_t counts);

// Tilt about the x and y axes from a (mean) gravity vector. Returns degrees.
void achal_tilt_deg(float mx, float my, float mz, float *tilt_x, float *tilt_y);

// First-order low-pass (exponential moving average) used on tilt between
// windows: y += alpha * (x - y), alpha = dt / (tau + dt). Pass isnan(prev) to start.
float achal_ema(float prev, float x, float alpha);

// --- Vibration features --------------------------------------------------------

typedef struct {
    float rms_g;     // RMS of the window with its mean removed
    float peak_g;    // largest |a - mean|
    float ppv_mm_s;  // peak particle velocity: a integrated, line-detrended over the window
    float dom_hz;    // dominant frequency from hysteresis zero crossings of a - mean
} achal_vib_t;

// a_g: n samples of the vertical axis at fs_hz. n >= 2.
void achal_vib_features(const float *a_g, uint16_t n, float fs_hz, achal_vib_t *out);

// DGMS (Tech)(S&T) Circular 7 of 1997, permissible PPV for domestic houses
// and structures (kuchha, brick and cement) by dominant frequency.
float achal_dgms_ppv_limit_mm_s(float dom_hz);

// --- VL53L0X ------------------------------------------------------------------

// RangeStatus as reported by the VL53L0X API (UM2039).
enum {
    ACHAL_TOF_VALID = 0,
    ACHAL_TOF_SIGMA_FAIL = 1,
    ACHAL_TOF_SIGNAL_FAIL = 2,
    ACHAL_TOF_MIN_RANGE_FAIL = 3,
    ACHAL_TOF_PHASE_FAIL = 4,  // out of range / wrap-around
    ACHAL_TOF_HW_FAIL = 5,
    ACHAL_TOF_TIMEOUT = 255,   // driver timed out, no measurement
};

typedef struct {
    uint16_t range_mm;    // 8190/8191 when the sensor reports out of range
    uint8_t status;       // ACHAL_TOF_*
    float signal_mcps;    // return signal rate, mega counts per second
    float sigma_mm;       // the sensor's own sigma estimate
} achal_tof_sample_t;

typedef struct {
    uint8_t min_valid;        // fewer valid samples than this -> no reading this window
    float max_sigma_mm;       // reject samples whose sigma estimate is above this
    float min_signal_mcps;    // reject weak returns (ST default limit 0.25 MCPS)
    uint16_t max_range_mm;    // geometry limit of the installation (target plate distance + margin)
} achal_tof_cfg_t;

// Median of the samples that pass every check. Returns 1 and writes *mm when at
// least min_valid samples pass, else 0. *n_valid always receives the count.
// Median of the last three filtered ToF readings: one bad reading can never
// raise a displacement alarm on its own (the detector runs every window, the ToF
// only every tof_every_s, so a stale outlier would otherwise satisfy persist_n).
float achal_median3(float a, float b, float c);

int achal_tof_filter(const achal_tof_cfg_t *cfg, const achal_tof_sample_t *s, uint8_t n, float *mm,
                     uint8_t *n_valid);

// --- Event detection ----------------------------------------------------------

enum { ACHAL_LVL_NORMAL = 0, ACHAL_LVL_WARNING = 1, ACHAL_LVL_CRITICAL = 2 };
enum { ACHAL_CAUSE_TILT = 1, ACHAL_CAUSE_DISP = 2, ACHAL_CAUSE_VIB = 4 };

typedef struct {
    float tilt_warn_deg, tilt_crit_deg;    // change of tilt magnitude from the install baseline
    float disp_warn_mm, disp_crit_mm;      // change of ToF distance from the install baseline
    float ppv_warn_scale;                  // vibration warning at scale * DGMS limit (1.0 = the limit)
    uint8_t vib_can_go_critical;           // 0: vibration alone never exceeds WARNING
    uint8_t persist_n;                     // consecutive windows at a level before raising to it
    uint8_t clear_n;                       // consecutive quiet windows before dropping one level
    float hysteresis;                      // quiet = below hysteresis * warning threshold
} achal_det_cfg_t;

typedef struct {
    uint8_t level;          // ACHAL_LVL_*
    uint8_t cause;          // ACHAL_CAUSE_* bits behind the current level
    uint8_t run_warn;       // consecutive windows with candidate >= WARNING
    uint8_t run_crit;       // consecutive windows with candidate == CRITICAL
    uint8_t run_quiet;      // consecutive quiet windows
} achal_det_state_t;

void achal_det_default(achal_det_cfg_t *cfg);
void achal_det_reset(achal_det_state_t *st);

// One detection step per reporting window. disp_valid = 0 when the ToF gave no
// reading (the displacement channel is then ignored, never treated as zero).
// Returns the new level.
uint8_t achal_detect(const achal_det_cfg_t *cfg, achal_det_state_t *st, float tilt_change_deg,
                     float disp_change_mm, int disp_valid, float ppv_mm_s, float dom_hz);

#ifdef __cplusplus
}
#endif

#endif
