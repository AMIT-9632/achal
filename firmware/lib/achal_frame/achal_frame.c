// SPDX-License-Identifier: MIT
#include "achal_frame.h"

#include <string.h>

#define HDR_LEN 5
#define CRC_LEN 2
#define BEACON_LEN 13
#define ACK_LEN 3
#define TEL_FIXED_LEN 32  // telemetry payload without the neighbour list

uint16_t achal_crc16(const uint8_t *buf, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)buf[i] << 8;
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

// FNV-1a folded to 8 bits. Collisions only matter for two sites in radio range.
uint8_t achal_site_hash(const char *site_id) {
    uint32_t h = 2166136261u;
    for (const char *p = site_id; *p; p++) {
        h ^= (uint8_t)*p;
        h *= 16777619u;
    }
    return (uint8_t)(h ^ (h >> 8) ^ (h >> 16) ^ (h >> 24));
}

static void put16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *p, uint32_t v) {
    put16(p, (uint16_t)v);
    put16(p + 2, (uint16_t)(v >> 16));
}

static uint16_t get16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }

static uint32_t get32(const uint8_t *p) { return get16(p) | ((uint32_t)get16(p + 2) << 16); }

size_t achal_encode(const achal_frame_t *f, uint8_t *buf, size_t cap) {
    size_t n;
    if (f->type == ACHAL_BEACON) {
        n = HDR_LEN + BEACON_LEN + CRC_LEN;
    } else if (f->type == ACHAL_ACK) {
        n = HDR_LEN + ACK_LEN + CRC_LEN;
    } else if (f->type == ACHAL_TELEMETRY) {
        if (f->u.tel.n_nbr > ACHAL_MAX_NBR) return 0;
        n = HDR_LEN + TEL_FIXED_LEN + 2u * f->u.tel.n_nbr + CRC_LEN;
    } else {
        return 0;
    }
    if (cap < n) return 0;

    buf[0] = ACHAL_MAGIC;
    buf[1] = (uint8_t)((ACHAL_VERSION << 4) | (f->type & 0x0F));
    buf[2] = f->site;
    buf[3] = f->sender;
    buf[4] = f->dest;
    uint8_t *p = buf + HDR_LEN;

    if (f->type == ACHAL_BEACON) {
        const achal_beacon_t *b = &f->u.beacon;
        put32(p, b->epoch);
        put16(p + 4, b->period_s);
        put16(p + 6, b->bseq);
        p[8] = b->hops;
        p[9] = b->siren_mask;
        p[10] = b->siren_level;
        put16(p + 11, b->tx_offset_ms);
        p += BEACON_LEN;
    } else if (f->type == ACHAL_ACK) {
        p[0] = f->u.ack.origin;
        put16(p + 1, f->u.ack.seq);
        p += ACK_LEN;
    } else {
        const achal_telemetry_t *t = &f->u.tel;
        p[0] = t->origin;
        put16(p + 1, t->seq);
        p[3] = t->hop_count;
        memcpy(p + 4, t->path, ACHAL_MAX_PATH);
        put32(p + 8, t->sample_epoch);
        put16(p + 12, (uint16_t)t->tilt_x_mdeg);
        put16(p + 14, (uint16_t)t->tilt_y_mdeg);
        put16(p + 16, t->vib_rms_100ug);
        put16(p + 18, t->vib_peak_mg);
        put16(p + 20, (uint16_t)t->crack_10um);
        put16(p + 22, (uint16_t)t->ext_10um);
        put16(p + 24, (uint16_t)t->temp_cc);
        put16(p + 26, t->batt_mv);
        p[28] = t->flags;
        p[29] = t->n_nbr;
        p[30] = 0;  // reserved
        p[31] = 0;
        p += TEL_FIXED_LEN;
        for (uint8_t i = 0; i < t->n_nbr; i++) {
            *p++ = t->nbr[i].id;
            *p++ = (uint8_t)t->nbr[i].rssi_dbm;
        }
    }
    put16(p, achal_crc16(buf, (size_t)(p - buf)));
    return n;
}

achal_status_t achal_decode(const uint8_t *buf, size_t len, achal_frame_t *out) {
    if (len < HDR_LEN + CRC_LEN) return ACHAL_E_SHORT;
    if (buf[0] != ACHAL_MAGIC) return ACHAL_E_MAGIC;
    if ((buf[1] >> 4) != ACHAL_VERSION) return ACHAL_E_VERSION;
    if (achal_crc16(buf, len - CRC_LEN) != get16(buf + len - CRC_LEN)) return ACHAL_E_CRC;

    memset(out, 0, sizeof(*out));
    out->type = (achal_type_t)(buf[1] & 0x0F);
    out->site = buf[2];
    out->sender = buf[3];
    out->dest = buf[4];
    const uint8_t *p = buf + HDR_LEN;
    size_t body = len - HDR_LEN - CRC_LEN;

    if (out->type == ACHAL_BEACON) {
        if (body != BEACON_LEN) return ACHAL_E_LENGTH;
        achal_beacon_t *b = &out->u.beacon;
        b->epoch = get32(p);
        b->period_s = get16(p + 4);
        b->bseq = get16(p + 6);
        b->hops = p[8];
        b->siren_mask = p[9];
        b->siren_level = p[10];
        b->tx_offset_ms = get16(p + 11);
        return ACHAL_OK;
    }
    if (out->type == ACHAL_ACK) {
        if (body != ACK_LEN) return ACHAL_E_LENGTH;
        out->u.ack.origin = p[0];
        out->u.ack.seq = get16(p + 1);
        return ACHAL_OK;
    }
    if (out->type == ACHAL_TELEMETRY) {
        if (body < TEL_FIXED_LEN) return ACHAL_E_LENGTH;
        achal_telemetry_t *t = &out->u.tel;
        t->origin = p[0];
        t->seq = get16(p + 1);
        t->hop_count = p[3];
        memcpy(t->path, p + 4, ACHAL_MAX_PATH);
        t->sample_epoch = get32(p + 8);
        t->tilt_x_mdeg = (int16_t)get16(p + 12);
        t->tilt_y_mdeg = (int16_t)get16(p + 14);
        t->vib_rms_100ug = get16(p + 16);
        t->vib_peak_mg = get16(p + 18);
        t->crack_10um = (int16_t)get16(p + 20);
        t->ext_10um = (int16_t)get16(p + 22);
        t->temp_cc = (int16_t)get16(p + 24);
        t->batt_mv = get16(p + 26);
        t->flags = p[28];
        t->n_nbr = p[29];
        if (t->n_nbr > ACHAL_MAX_NBR || body != TEL_FIXED_LEN + 2u * t->n_nbr) return ACHAL_E_LENGTH;
        p += TEL_FIXED_LEN;
        for (uint8_t i = 0; i < t->n_nbr; i++) {
            t->nbr[i].id = p[2 * i];
            t->nbr[i].rssi_dbm = (int8_t)p[2 * i + 1];
        }
        return ACHAL_OK;
    }
    return ACHAL_E_TYPE;
}
