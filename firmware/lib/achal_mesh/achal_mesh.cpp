// SPDX-License-Identifier: MIT
#include "achal_mesh.h"

#include <math.h>

namespace achal {

bool DupCache::seen_or_add(uint8_t origin, uint16_t seq) {
    uint32_t key = 0x01000000u | ((uint32_t)origin << 16) | seq;  // top byte keeps 0 meaning "empty"
    for (uint32_t k : keys_) {
        if (k == key) return true;
    }
    keys_[next_] = key;
    next_ = (uint8_t)((next_ + 1) % kSize);
    return false;
}

const Neighbour *Neighbours::find(uint8_t id) const {
    for (const Neighbour &n : table_) {
        if (n.id == id) return &n;
    }
    return nullptr;
}

bool Neighbours::fresh(const Neighbour &n, uint16_t bseq) const {
    return n.id != kNoRoute && (uint16_t)(bseq - n.last_bseq) < kStaleBeacons;
}

void Neighbours::heard(uint8_t id, int8_t rssi, uint8_t hops, uint16_t bseq) {
    Neighbour *slot = nullptr;
    Neighbour *oldest = &table_[0];
    for (Neighbour &n : table_) {
        if (n.id == id) {
            slot = &n;
            break;
        }
        if (slot == nullptr && n.id == kNoRoute) slot = &n;
        if ((uint16_t)(bseq - n.last_bseq) > (uint16_t)(bseq - oldest->last_bseq)) oldest = &n;
    }
    if (slot == nullptr) slot = oldest;  // table full: replace the longest-silent entry
    if (slot->id != id) {
        *slot = Neighbour();
        slot->id = id;
        slot->rssi = rssi;
    } else {
        slot->rssi = (int8_t)((3 * slot->rssi + rssi) / 4);  // EWMA, alpha = 1/4
    }
    if (hops != kNoRoute) slot->hops = hops;
    slot->last_bseq = bseq;
}

void Neighbours::link_failed(uint8_t id) {
    for (Neighbour &n : table_) {
        if (n.id == id && n.fails < 255) n.fails++;
    }
}

void Neighbours::link_ok(uint8_t id) {
    for (Neighbour &n : table_) {
        if (n.id == id) n.fails = 0;
    }
}

uint8_t Neighbours::parent(uint16_t bseq, uint8_t exclude) const {
    const Neighbour *best = nullptr;
    bool best_ok = false;
    for (const Neighbour &n : table_) {
        if (!fresh(n, bseq) || n.hops == kNoRoute || n.id == exclude) continue;
        bool ok = n.fails < kLinkFailLimit && n.rssi > kWeakLinkDbm;
        if (best == nullptr || (ok && !best_ok) ||
            (ok == best_ok && (n.hops < best->hops || (n.hops == best->hops && n.rssi > best->rssi)))) {
            best = &n;
            best_ok = ok;
        }
    }
    return best ? best->id : kNoRoute;
}

uint8_t Neighbours::my_hops(uint16_t bseq) const {
    const Neighbour *p = find(parent(bseq));
    return p ? (uint8_t)(p->hops + 1) : kNoRoute;
}

uint8_t Neighbours::strongest(achal_nbr_t *out, uint8_t max) const {
    uint8_t n_out = 0;
    bool used[kMaxNeighbours] = {};
    while (n_out < max) {
        int pick = -1;
        for (int i = 0; i < kMaxNeighbours; i++) {
            const Neighbour &n = table_[i];
            if (used[i] || n.id == kNoRoute || n.id == ACHAL_GATEWAY_ID) continue;
            if (pick < 0 || n.rssi > table_[pick].rssi) pick = i;
        }
        if (pick < 0) break;
        used[pick] = true;
        out[n_out].id = table_[pick].id;
        out[n_out].rssi_dbm = table_[pick].rssi;
        n_out++;
    }
    return n_out;
}

uint32_t slot_offset_ms(uint8_t node_id) { return kBeaconPhaseMs + (uint32_t)(node_id - 1) * kSlotMs; }

uint32_t window_end_ms(uint8_t n_nodes) { return kBeaconPhaseMs + 2u * n_nodes * kSlotMs; }

uint32_t beacon_relay_delay_ms(uint8_t my_hops, uint32_t rand32) {
    // Ring h relays inside [h-1, h) x 400 ms, at a random point so neighbours rarely overlap.
    uint32_t ring = my_hops > 0 ? my_hops - 1u : 0u;
    return ring * 400u + 20u + rand32 % 260u;
}

uint32_t lora_airtime_ms(uint8_t payload_len, uint8_t sf, uint32_t bw_hz, uint8_t cr_denom, uint16_t preamble) {
    double t_sym = (double)(1u << sf) / (double)bw_hz * 1000.0;
    int de = (t_sym > 16.0) ? 1 : 0;  // low data rate optimise, mandatory above 16 ms symbols
    double num = 8.0 * payload_len - 4.0 * sf + 28 + 16;  // explicit header, CRC on
    double den = 4.0 * (sf - 2 * de);
    double n_payload = 8 + fmax(ceil(num / den) * cr_denom, 0);
    double t = (preamble + 4.25 + n_payload) * t_sym;
    return (uint32_t)ceil(t);
}

}  // namespace achal
