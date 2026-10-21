// SPDX-License-Identifier: MIT
#include "achal_radio.h"

#include <Arduino.h>
#include <RadioLib.h>
#include <SPI.h>

namespace {

SX1276 *chip = nullptr;
volatile bool rx_flag = false;

void IRAM_ATTR on_rx() { rx_flag = true; }

}  // namespace

namespace radio {

bool begin(const RadioPins &pins, const RadioParams &p) {
    SPI.begin(pins.sck, pins.miso, pins.mosi, pins.nss);
    static Module mod(pins.nss, pins.dio0, pins.rst, pins.dio1);
    static SX1276 sx(&mod);
    chip = &sx;
    int16_t st = chip->begin(p.freq_mhz, p.bw_khz, p.sf, p.cr, p.sync, p.dbm, p.preamble);
    if (st != RADIOLIB_ERR_NONE) return false;
    chip->setCRC(true);
    chip->setPacketReceivedAction(on_rx);
    return chip->startReceive() == RADIOLIB_ERR_NONE;
}

bool send(const achal_frame_t &f, uint8_t tries) {
    uint8_t buf[ACHAL_MAX_FRAME];
    size_t n = achal_encode(&f, buf, sizeof(buf));
    if (n == 0 || chip == nullptr) return false;
    bool sent = false;
    for (uint8_t i = 0; i < tries && !sent; i++) {
        if (chip->scanChannel() == RADIOLIB_CHANNEL_FREE) {
            sent = chip->transmit(buf, n) == RADIOLIB_ERR_NONE;
        } else {
            delay(random(15, 60));  // someone is talking: back off and listen again
        }
    }
    rx_flag = false;
    chip->startReceive();
    return sent;
}

bool poll(RxFrame &out, uint8_t site) {
    if (!rx_flag || chip == nullptr) return false;
    rx_flag = false;
    uint8_t buf[ACHAL_MAX_FRAME];
    size_t n = chip->getPacketLength();
    bool ok = n <= sizeof(buf) && chip->readData(buf, n) == RADIOLIB_ERR_NONE &&
              achal_decode(buf, n, &out.f) == ACHAL_OK && out.f.site == site;
    out.rssi_dbm = chip->getRSSI();
    out.snr_db = chip->getSNR();
    chip->startReceive();
    return ok;
}

void sleep() {
    if (chip) chip->sleep();
}

void listen() {
    if (chip == nullptr) return;
    rx_flag = false;
    chip->startReceive();
}

}  // namespace radio
