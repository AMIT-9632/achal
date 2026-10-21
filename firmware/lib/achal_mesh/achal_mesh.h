// SPDX-License-Identifier: MIT
// Routing and timing for the ACHAL wireless surface mesh. Pure C++ with no
// Arduino calls, so the same code runs on the ESP32 and in `pio test -e native`.
//
// Scheme (see firmware/README.md for the full description):
// - The gateway floods a BEACON at the start of every period. Each node
//   rebroadcasts the first copy it hears once, after a short random delay,
//   with its own hop distance. That gives every node the time and a gradient
//   towards the gateway.
// - Each node picks a parent: the neighbour with the fewest hops to the
//   gateway, ties broken by RSSI, skipping links that recently failed.
// - In its own TDMA slot a node sends its reading to its parent. The parent
//   relays it straight on to its own parent. The sender overhears that relay
//   as an implicit acknowledgement; if it hears nothing it marks the link as
//   failed and retries once through the next-best neighbour (route repair).
#ifndef ACHAL_MESH_H
#define ACHAL_MESH_H

#include <stdint.h>

#include "achal_frame.h"

namespace achal {

constexpr uint8_t kNoRoute = 0xFF;
constexpr uint8_t kMaxNeighbours = 16;
constexpr uint8_t kLinkFailLimit = 2;      // failed sends before a parent is avoided
constexpr uint16_t kStaleBeacons = 3;      // forget routes not refreshed for this many beacons
constexpr int8_t kWeakLinkDbm = -118;      // only used when nothing better exists

// Timing inside one period, in ms from the gateway beacon.
constexpr uint32_t kBeaconPhaseMs = 2000;  // beacon flood (4 hops x ~400 ms incl. backoff)
constexpr uint32_t kSlotMs = 250;          // one node's own frame + room for relays
constexpr uint32_t kGuardMs = 300;         // wake this early to absorb RTC drift

// Remembers recent (origin, seq) pairs so a frame is relayed or forwarded once.
class DupCache {
public:
    bool seen_or_add(uint8_t origin, uint16_t seq);

private:
    static constexpr uint8_t kSize = 48;
    uint32_t keys_[kSize] = {};
    uint8_t next_ = 0;
};

struct Neighbour {
    uint8_t id = kNoRoute;
    int8_t rssi = -128;       // smoothed RSSI of frames heard from it
    uint8_t hops = kNoRoute;  // its advertised distance to the gateway
    uint16_t last_bseq = 0;   // last beacon round it was heard in
    uint8_t fails = 0;
};

class Neighbours {
public:
    // Any frame heard from `id`. `hops` comes from beacons; pass kNoRoute otherwise.
    void heard(uint8_t id, int8_t rssi, uint8_t hops, uint16_t bseq);
    void link_failed(uint8_t id);
    void link_ok(uint8_t id);

    // Best next hop towards the gateway, or kNoRoute. `exclude` skips one id (for the retry).
    uint8_t parent(uint16_t bseq, uint8_t exclude = kNoRoute) const;
    // My hop distance given the current parent (gateway's direct neighbours are 1).
    uint8_t my_hops(uint16_t bseq) const;
    // Strongest neighbours for the telemetry neighbour_rssi field. Returns how many were written.
    uint8_t strongest(achal_nbr_t *out, uint8_t max) const;

    const Neighbour *find(uint8_t id) const;

private:
    bool fresh(const Neighbour &n, uint16_t bseq) const;
    Neighbour table_[kMaxNeighbours];
};

// Offset of a node's TX slot from the beacon, in ms. Node ids start at 1.
uint32_t slot_offset_ms(uint8_t node_id);
// End of the data window for a site with `n_nodes` nodes, plus one retry slot for every node.
uint32_t window_end_ms(uint8_t n_nodes);
// Rebroadcast delay for the beacon flood: grows with hop count so rings do not collide.
uint32_t beacon_relay_delay_ms(uint8_t my_hops, uint32_t rand32);

// LoRa time on air in ms (Semtech AN1200.13), used for the power budget and slot sizing.
uint32_t lora_airtime_ms(uint8_t payload_len, uint8_t sf, uint32_t bw_hz, uint8_t cr_denom, uint16_t preamble);

}  // namespace achal

#endif
