// SPDX-License-Identifier: MIT
#pragma once

#include <stdint.h>

struct Reading {
    float tilt_x_deg = 0, tilt_y_deg = 0;  // relative to the install baseline
    float vib_rms_g = 0, vib_peak_g = 0;
    float crack_mm = 0, ext_mm = 0;        // relative to the install baseline
    float temp_c = 0;
    uint16_t batt_mv = 0;
    uint16_t tof_mm = 0;                    // VL53L0X gauge distance, median of a burst; 0 = no reading
    uint8_t flags = 0;                      // ACHAL_F_* bits
};

namespace sensors {

// Powers the switched rail, configures the chips. Safe to call every wake.
void begin();
// Takes one full reading (about 2.2 s: 2 s vibration window + ADC).
Reading sample();
// Re-zeroes tilt, crack and extensometer and stores the baseline in flash.
void store_baseline(const Reading &raw_now);
bool has_baseline();
// Arms the MPU6050 motion interrupt and cuts the switched rail before deep sleep.
void sleep_arm_motion_wake();
// After a motion wake: peak |a| deviation over a short burst, in g.
float burst_peak_g();

}  // namespace sensors
