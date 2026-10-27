# SPDX-License-Identifier: MIT
"""Python mirror of firmware/lib/achal_frame (over-the-air frame v1).

Used by tests (cross-checks the C codec through a shared golden vector) and by
tools that want to read raw radio frames on the Raspberry Pi.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime

MAGIC = 0xAC
VERSION = 1
BEACON, TELEMETRY, ACK = 1, 2, 3
GATEWAY_ID = 0
BROADCAST = 0xFF
MAX_PATH = 4
MAX_NBR = 6

_HDR = struct.Struct("<BBBBB")
_BEACON = struct.Struct("<IHHBBBH")
_ACK = struct.Struct("<BH")
_TEL = struct.Struct("<BHB4sIhhHHhhhHBBH")  # last H: tof_mm (0 = no reading), reserved before 2026-10-27

FLAGS = {
    0x01: "tilt_sensor",
    0x02: "adc",
    0x04: "low_batt",
    0x08: "tamper",
    0x10: "no_sync",
    0x20: "motion_wake",
    0x40: "tof",
}


class FrameError(ValueError):
    pass


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def site_hash(site_id: str) -> int:
    h = 2166136261
    for byte in site_id.encode():
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return (h ^ (h >> 8) ^ (h >> 16) ^ (h >> 24)) & 0xFF


@dataclass
class Telemetry:
    origin: int
    seq: int
    sample_epoch: int
    tilt_x_mdeg: int
    tilt_y_mdeg: int
    vib_rms_100ug: int
    vib_peak_mg: int
    crack_10um: int
    ext_10um: int
    temp_cc: int
    batt_mv: int
    flags: int = 0
    hop_count: int = 0
    path: list[int] = field(default_factory=list)
    nbr: list[tuple[int, int]] = field(default_factory=list)
    tof_mm: int = 0  # VL53L0X gauge distance, mm; 0 = no reading. Not in telemetry.v1 (proposed for v2)


@dataclass
class Frame:
    type: int
    site: int
    sender: int
    dest: int
    body: object


def encode(frame: Frame) -> bytes:
    out = _HDR.pack(MAGIC, (VERSION << 4) | frame.type, frame.site, frame.sender, frame.dest)
    b = frame.body
    if frame.type == BEACON:
        out += _BEACON.pack(*b)
    elif frame.type == ACK:
        out += _ACK.pack(*b)
    elif frame.type == TELEMETRY:
        if len(b.nbr) > MAX_NBR or len(b.path) > MAX_PATH:
            raise FrameError("too many neighbours or relays")
        path = bytes(b.path) + bytes(MAX_PATH - len(b.path))
        out += _TEL.pack(b.origin, b.seq, b.hop_count, path, b.sample_epoch, b.tilt_x_mdeg, b.tilt_y_mdeg,
                         b.vib_rms_100ug, b.vib_peak_mg, b.crack_10um, b.ext_10um, b.temp_cc, b.batt_mv,
                         b.flags, len(b.nbr), b.tof_mm)
        for node_id, rssi in b.nbr:
            out += struct.pack("<Bb", node_id, rssi)
    else:
        raise FrameError(f"unknown type {frame.type}")
    return out + struct.pack("<H", crc16(out))


def decode(data: bytes) -> Frame:
    if len(data) < _HDR.size + 2:
        raise FrameError("short frame")
    magic, ver_type, site, sender, dest = _HDR.unpack_from(data)
    if magic != MAGIC:
        raise FrameError("bad magic")
    if ver_type >> 4 != VERSION:
        raise FrameError("bad version")
    if crc16(data[:-2]) != struct.unpack_from("<H", data, len(data) - 2)[0]:
        raise FrameError("bad crc")
    ftype, payload = ver_type & 0x0F, data[_HDR.size:-2]
    if ftype == BEACON and len(payload) == _BEACON.size:
        body: object = _BEACON.unpack(payload)
    elif ftype == ACK and len(payload) == _ACK.size:
        body = _ACK.unpack(payload)
    elif ftype == TELEMETRY and len(payload) >= _TEL.size:
        (origin, seq, hop_count, path, epoch, tx, ty, vr, vp, cr, ex, tc, bv, flags, n_nbr, tof) = _TEL.unpack_from(
            payload
        )
        if n_nbr > MAX_NBR or len(payload) != _TEL.size + 2 * n_nbr:
            raise FrameError("bad length")
        nbr = [struct.unpack_from("<Bb", payload, _TEL.size + 2 * i) for i in range(n_nbr)]
        body = Telemetry(origin, seq, epoch, tx, ty, vr, vp, cr, ex, tc, bv, flags, hop_count,
                         list(path[:hop_count]), nbr, tof)
    else:
        raise FrameError(f"bad type or length ({ftype}, {len(payload)} B)")
    return Frame(ftype, site, sender, dest, body)


def to_json_dict(t: Telemetry, site: str, rssi_dbm: float | None = None, snr_db: float | None = None) -> dict:
    """The telemetry.v1 object the gateway firmware prints for this frame (without rx_ts)."""
    def name(i: int) -> str:
        return f"n{i:02d}"

    d = {
        "v": 1, "site": site, "node": name(t.origin), "seq": t.seq,
        "ts": datetime.fromtimestamp(t.sample_epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hops": t.hop_count + 1, "path": [name(t.origin)] + [name(p) for p in t.path],
        "neighbour_rssi": {name(i): float(r) for i, r in t.nbr},
        "tilt_x_deg": t.tilt_x_mdeg / 1000, "tilt_y_deg": t.tilt_y_mdeg / 1000,
        "vib_rms_g": t.vib_rms_100ug / 10000, "vib_peak_g": t.vib_peak_mg / 1000,
        "crack_mm": t.crack_10um / 100, "ext_mm": t.ext_10um / 100,
        "temp_c": t.temp_cc / 100, "batt_v": t.batt_mv / 1000,
    }
    if rssi_dbm is not None:
        d["rssi_dbm"] = rssi_dbm
    if snr_db is not None:
        d["snr_db"] = snr_db
    return d
