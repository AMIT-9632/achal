// SPDX-License-Identifier: MIT
// Thin wrapper over RadioLib's SX1276 driver shared by node and gateway:
// listen-before-talk with channel activity detection, interrupt-driven receive.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "achal_frame.h"

struct RadioPins {
    int sck, miso, mosi, nss, rst, dio0, dio1;
};

struct RadioParams {
    float freq_mhz, bw_khz;
    uint8_t sf, cr, sync;
    int8_t dbm;
    uint16_t preamble;
};

struct RxFrame {
    achal_frame_t f;
    float rssi_dbm;
    float snr_db;
};

namespace radio {

bool begin(const RadioPins &pins, const RadioParams &params);
// Encodes and sends a frame after the channel is clear (up to `tries` CAD + random backoff rounds).
bool send(const achal_frame_t &f, uint8_t tries = 4);
// Returns true and fills `out` if a valid frame for `site` arrived since the last call.
bool poll(RxFrame &out, uint8_t site);
// Puts the SX1276 to sleep (0.2 uA); listen() wakes it back into continuous receive.
void sleep();
void listen();

}  // namespace radio
