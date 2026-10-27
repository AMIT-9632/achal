// SPDX-License-Identifier: MIT
// Host tests for lib/achal_dsp (pio test -e native). The Python simulator's
// tests (services/hw-sim/tests) exercise the same compiled code through ctypes.
#include <math.h>
#include <unity.h>

#include "achal_dsp.h"

void setUp() {}
void tearDown() {}

void test_counts_and_temp() {
    TEST_ASSERT_FLOAT_WITHIN(1e-6, 1.0f, achal_mpu_counts_to_g(16384, 2));
    TEST_ASSERT_FLOAT_WITHIN(1e-6, -1.0f, achal_mpu_counts_to_g(-2048, 16));
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 36.53f, achal_mpu_temp_c(0));
    TEST_ASSERT_FLOAT_WITHIN(1e-6, 0.99f, achal_accel_temp_comp(1.0f, 0.001f, 35.0f, 25.0f));
}

void test_tilt() {
    float tx, ty;
    achal_tilt_deg(0, 0, 1, &tx, &ty);
    TEST_ASSERT_FLOAT_WITHIN(1e-5, 0, tx);
    float g = sinf(0.5f * 3.14159265f / 180.0f);
    achal_tilt_deg(g, 0, cosf(0.5f * 3.14159265f / 180.0f), &tx, &ty);
    TEST_ASSERT_FLOAT_WITHIN(1e-3, 0.5f, tx);
    TEST_ASSERT_FLOAT_WITHIN(1e-5, 0, ty);
}

void test_vibration_ppv_and_frequency() {
    static float a[200];
    const int freqs[] = {6, 10, 15};
    for (int f : freqs) {
        for (int i = 0; i < 200; i++) a[i] = 0.02f * sinf(2 * 3.14159265f * f * i / 200.0f + 0.4f);
        achal_vib_t v;
        achal_vib_features(a, 200, 200.0f, &v);
        float ppv = 0.02f * 9806.65f / (2 * 3.14159265f * f);
        TEST_ASSERT_FLOAT_WITHIN(0.08f * ppv, ppv, v.ppv_mm_s);
        TEST_ASSERT_FLOAT_WITHIN(1.0f, (float)f, v.dom_hz);
        TEST_ASSERT_FLOAT_WITHIN(1e-3, 0.02f / sqrtf(2.0f), v.rms_g);
    }
}

void test_dgms_limits() {
    TEST_ASSERT_EQUAL_FLOAT(5.0f, achal_dgms_ppv_limit_mm_s(4));
    TEST_ASSERT_EQUAL_FLOAT(10.0f, achal_dgms_ppv_limit_mm_s(20));
    TEST_ASSERT_EQUAL_FLOAT(15.0f, achal_dgms_ppv_limit_mm_s(40));
}

void test_tof_filter_rejects_invalid() {
    achal_tof_cfg_t cfg = {3, 15.0f, 0.25f, 1200};
    achal_tof_sample_t s[6] = {{500, 0, 2, 3}, {8190, 4, 0, 0}, {502, 0, 2, 3}, {120, 0, 0.1f, 3},
                               {498, 0, 2, 40}, {501, 0, 2, 3}};
    float mm;
    uint8_t nv;
    TEST_ASSERT_EQUAL_INT(1, achal_tof_filter(&cfg, s, 6, &mm, &nv));
    TEST_ASSERT_EQUAL_UINT8(3, nv);  // out of range, weak signal and high sigma all rejected
    TEST_ASSERT_EQUAL_FLOAT(501.0f, mm);
    cfg.min_valid = 4;
    TEST_ASSERT_EQUAL_INT(0, achal_tof_filter(&cfg, s, 6, &mm, &nv));
    TEST_ASSERT_EQUAL_FLOAT(7.0f, achal_median3(9, 7, 1));
}

void test_detector_persistence_hysteresis_and_vibration_cap() {
    achal_det_cfg_t c;
    achal_det_default(&c);
    achal_det_state_t st;
    achal_det_reset(&st);
    TEST_ASSERT_EQUAL_UINT8(0, achal_detect(&c, &st, 0.15f, 0, 0, 0, 0));
    TEST_ASSERT_EQUAL_UINT8(0, achal_detect(&c, &st, 0.15f, 0, 0, 0, 0));
    TEST_ASSERT_EQUAL_UINT8(1, achal_detect(&c, &st, 0.15f, 0, 0, 0, 0));
    TEST_ASSERT_EQUAL_UINT8(ACHAL_CAUSE_TILT, st.cause);
    for (int i = 0; i < 3; i++) achal_detect(&c, &st, 0.5f, 0, 0, 0, 0);
    TEST_ASSERT_EQUAL_UINT8(2, st.level);
    // 0.09 deg is below warning but above 0.8 x warning: not quiet, level holds
    for (int i = 0; i < 20; i++) achal_detect(&c, &st, 0.09f, 0, 0, 0, 0);
    TEST_ASSERT_EQUAL_UINT8(2, st.level);
    for (int i = 0; i < 10; i++) achal_detect(&c, &st, 0.0f, 0, 0, 0, 0);
    TEST_ASSERT_EQUAL_UINT8(1, st.level);
    // vibration alone never goes critical by default
    achal_det_reset(&st);
    for (int i = 0; i < 10; i++) achal_detect(&c, &st, 0, 0, 0, 50.0f, 5.0f);
    TEST_ASSERT_EQUAL_UINT8(1, st.level);
    TEST_ASSERT_EQUAL_UINT8(ACHAL_CAUSE_VIB, st.cause);
    // an invalid displacement is ignored, not read as zero or as huge
    achal_det_reset(&st);
    for (int i = 0; i < 10; i++) achal_detect(&c, &st, 0, 999.0f, 0, 0, 0);
    TEST_ASSERT_EQUAL_UINT8(0, st.level);
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_counts_and_temp);
    RUN_TEST(test_tilt);
    RUN_TEST(test_vibration_ppv_and_frequency);
    RUN_TEST(test_dgms_limits);
    RUN_TEST(test_tof_filter_rejects_invalid);
    RUN_TEST(test_detector_persistence_hysteresis_and_vibration_cap);
    return UNITY_END();
}
