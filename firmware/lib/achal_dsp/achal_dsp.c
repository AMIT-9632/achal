// SPDX-License-Identifier: MIT
#include "achal_dsp.h"

#include <math.h>

#define G_MM_S2 9806.65f
#define RAD2DEG 57.2957795f
#define PI_F 3.14159265f

float achal_mpu_counts_to_g(int16_t counts, uint8_t fs_g) {
    float lsb_per_g = fs_g >= 16 ? 2048.0f : fs_g >= 8 ? 4096.0f : fs_g >= 4 ? 8192.0f : 16384.0f;
    return (float)counts / lsb_per_g;
}

float achal_accel_temp_comp(float a_g, float k_g_per_c, float t_c, float t_ref_c) {
    return a_g - k_g_per_c * (t_c - t_ref_c);
}

float achal_mpu_temp_c(int16_t counts) { return (float)counts / 340.0f + 36.53f; }

void achal_tilt_deg(float mx, float my, float mz, float *tilt_x, float *tilt_y) {
    *tilt_x = atan2f(mx, sqrtf(my * my + mz * mz)) * RAD2DEG;
    *tilt_y = atan2f(my, sqrtf(mx * mx + mz * mz)) * RAD2DEG;
}

float achal_ema(float prev, float x, float alpha) { return isnan(prev) ? x : prev + alpha * (x - prev); }

void achal_vib_features(const float *a, uint16_t n, float fs_hz, achal_vib_t *out) {
    out->rms_g = out->peak_g = out->ppv_mm_s = out->dom_hz = 0.0f;
    if (n < 2 || fs_hz <= 0.0f) return;
    double mean = 0.0;
    for (uint16_t i = 0; i < n; i++) mean += a[i];
    mean /= n;
    const float dt = 1.0f / fs_hz;
    // Velocity by trapezoidal integration. The unknown initial velocity and any
    // residual accelerometer offset show up as a constant and a ramp in v, so a
    // least-squares line is removed in a second pass before taking the peak.
    double ss = 0.0, sv = 0.0, siv = 0.0;
    float v = 0.0f, peak = 0.0f, prev = 0.0f;
    uint16_t crossings = 0;
    for (uint16_t i = 0; i < n; i++) {
        float x = a[i] - (float)mean;
        ss += (double)x * x;
        if (fabsf(x) > peak) peak = fabsf(x);
        if (i > 0) v += 0.5f * (x + prev) * G_MM_S2 * dt;
        sv += v;
        siv += (double)i * v;
        prev = x;
    }
    const double si = (double)n * (n - 1) / 2.0, sii = (double)(n - 1) * n * (2.0 * n - 1) / 6.0;
    const double den = n * sii - si * si;
    const float slope = den != 0.0 ? (float)((n * siv - si * sv) / den) : 0.0f;
    const float icpt = (float)((sv - slope * si) / n);
    // Dominant frequency: zero crossings with a Schmitt trigger at 30 % of the peak,
    // so sensor noise riding on a low-frequency burst does not count as crossings.
    const float hyst = 0.3f * peak;
    int8_t side = 0;
    float ppv = 0.0f;
    v = 0.0f;
    prev = a[0] - (float)mean;
    for (uint16_t i = 0; i < n; i++) {
        float x = a[i] - (float)mean;
        if (i > 0) v += 0.5f * (x + prev) * G_MM_S2 * dt;
        float r = fabsf(v - (icpt + slope * (float)i));
        if (r > ppv) ppv = r;
        if (x > hyst && side <= 0) { if (side < 0) crossings++; side = 1; }
        else if (x < -hyst && side >= 0) { if (side > 0) crossings++; side = -1; }
        prev = x;
    }
    out->rms_g = sqrtf((float)(ss / n));
    out->peak_g = peak;
    out->ppv_mm_s = ppv;
    out->dom_hz = (float)crossings * fs_hz / (2.0f * (float)n);
}

float achal_dgms_ppv_limit_mm_s(float dom_hz) {
    if (dom_hz < 8.0f) return 5.0f;
    if (dom_hz <= 25.0f) return 10.0f;
    return 15.0f;
}

int achal_tof_filter(const achal_tof_cfg_t *cfg, const achal_tof_sample_t *s, uint8_t n, float *mm,
                     uint8_t *n_valid) {
    float buf[32];
    uint8_t k = 0;
    for (uint8_t i = 0; i < n && k < 32; i++) {
        const achal_tof_sample_t *x = &s[i];
        if (x->status != ACHAL_TOF_VALID) continue;
        if (x->range_mm >= 8190 || x->range_mm > cfg->max_range_mm) continue;
        if (x->sigma_mm > cfg->max_sigma_mm || x->signal_mcps < cfg->min_signal_mcps) continue;
        // insertion sort keeps buf ordered
        float r = (float)x->range_mm;
        int j = k - 1;
        while (j >= 0 && buf[j] > r) {
            buf[j + 1] = buf[j];
            j--;
        }
        buf[j + 1] = r;
        k++;
    }
    *n_valid = k;
    if (k == 0 || k < cfg->min_valid) return 0;
    *mm = (k & 1) ? buf[k / 2] : 0.5f * (buf[k / 2 - 1] + buf[k / 2]);
    return 1;
}

float achal_median3(float a, float b, float c) {
    if (a > b) { float t = a; a = b; b = t; }
    if (b > c) { float t = b; b = c; c = t; }
    if (a > b) { float t = a; a = b; b = t; }
    return b;
}

void achal_det_default(achal_det_cfg_t *c) {
    c->tilt_warn_deg = 0.10f;   // ~1.7 mm/m
    c->tilt_crit_deg = 0.30f;   // ~5.2 mm/m
    c->disp_warn_mm = 5.0f;
    c->disp_crit_mm = 15.0f;
    c->ppv_warn_scale = 1.0f;
    c->vib_can_go_critical = 0;
    c->persist_n = 3;
    c->clear_n = 10;
    c->hysteresis = 0.8f;
}

void achal_det_reset(achal_det_state_t *st) {
    st->level = ACHAL_LVL_NORMAL;
    st->cause = 0;
    st->run_warn = st->run_crit = st->run_quiet = 0;
}

static uint8_t sat_inc(uint8_t x) { return x == 255 ? 255 : (uint8_t)(x + 1); }

uint8_t achal_detect(const achal_det_cfg_t *c, achal_det_state_t *st, float tilt, float disp, int disp_valid,
                     float ppv, float dom_hz) {
    tilt = fabsf(tilt);
    disp = fabsf(disp);
    uint8_t cand = ACHAL_LVL_NORMAL, cause = 0;
    if (tilt >= c->tilt_crit_deg) { cand = ACHAL_LVL_CRITICAL; cause |= ACHAL_CAUSE_TILT; }
    else if (tilt >= c->tilt_warn_deg) { cand = cand > 1 ? cand : 1; cause |= ACHAL_CAUSE_TILT; }
    if (disp_valid) {
        if (disp >= c->disp_crit_mm) { cand = ACHAL_LVL_CRITICAL; cause |= ACHAL_CAUSE_DISP; }
        else if (disp >= c->disp_warn_mm) { cand = cand > 1 ? cand : 1; cause |= ACHAL_CAUSE_DISP; }
    }
    float ppv_lim = c->ppv_warn_scale * achal_dgms_ppv_limit_mm_s(dom_hz);
    if (ppv >= ppv_lim) {
        uint8_t v = (c->vib_can_go_critical && ppv >= 2.0f * ppv_lim) ? ACHAL_LVL_CRITICAL : ACHAL_LVL_WARNING;
        if (v > cand) cand = v;
        cause |= ACHAL_CAUSE_VIB;
    }

    st->run_warn = cand >= ACHAL_LVL_WARNING ? sat_inc(st->run_warn) : 0;
    st->run_crit = cand == ACHAL_LVL_CRITICAL ? sat_inc(st->run_crit) : 0;

    int quiet = tilt < c->hysteresis * c->tilt_warn_deg && (!disp_valid || disp < c->hysteresis * c->disp_warn_mm) &&
                ppv < c->hysteresis * ppv_lim;
    st->run_quiet = quiet ? sat_inc(st->run_quiet) : 0;

    if (st->run_crit >= c->persist_n && st->level < ACHAL_LVL_CRITICAL) {
        st->level = ACHAL_LVL_CRITICAL;
        st->cause = cause;
    } else if (st->run_warn >= c->persist_n && st->level < ACHAL_LVL_WARNING) {
        st->level = ACHAL_LVL_WARNING;
        st->cause = cause;
    } else if (st->level > ACHAL_LVL_NORMAL && st->run_quiet >= c->clear_n) {
        st->level--;
        st->run_quiet = 0;
        if (st->level == ACHAL_LVL_NORMAL) st->cause = 0;
    } else if (st->level > ACHAL_LVL_NORMAL) {
        st->cause |= cause;
    }
    return st->level;
}
