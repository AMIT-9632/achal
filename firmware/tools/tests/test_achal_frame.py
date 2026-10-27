# SPDX-License-Identifier: MIT
import achal_frame as af
import pytest
from achal_common import Telemetry
from uart_bridge import SirenState, route_line

# Same bytes as kGoldenTelemetryHex in firmware/test/test_native/test_main.cpp.
GOLDEN = "ac1290070c07430001120000002c5ed76a84fc0e01f4014b00c8ffc4097e09f60e2102000002d50cb0bab7"


def golden_telemetry() -> af.Telemetry:
    return af.Telemetry(origin=7, seq=67, sample_epoch=1792499244, tilt_x_mdeg=-892, tilt_y_mdeg=270,
                        vib_rms_100ug=500, vib_peak_mg=75, crack_10um=-56, ext_10um=2500, temp_cc=2430,
                        batt_mv=3830, flags=0x21, hop_count=1, path=[18], nbr=[(2, -43), (12, -80)])


def test_crc_check_value():
    assert af.crc16(b"123456789") == 0x29B1


def test_python_codec_matches_c_golden_vector():
    frame = af.Frame(af.TELEMETRY, af.site_hash("jharia-demo"), 7, 12, golden_telemetry())
    assert af.encode(frame).hex() == GOLDEN
    assert af.decode(bytes.fromhex(GOLDEN)) == frame


def test_decoded_frame_is_valid_telemetry_v1():
    frame = af.decode(bytes.fromhex(GOLDEN))
    reading = Telemetry.model_validate(af.to_json_dict(frame.body, "jharia-demo", -97.5, 6.2))
    assert reading.node == "n07"
    assert reading.path == ["n07", "n18"]
    assert reading.hops == 2
    assert reading.tilt_x_deg == pytest.approx(-0.892)
    assert reading.neighbour_rssi == {"n02": -43.0, "n12": -80.0}


def test_beacon_and_ack_roundtrip():
    beacon = af.Frame(af.BEACON, 0x90, 0, af.BROADCAST, (1792499220, 60, 513, 2, 3, 3, 712))
    raw = af.encode(beacon)
    assert len(raw) == 20
    assert af.decode(raw) == beacon
    ack = af.Frame(af.ACK, 0x90, 0, 7, (9, 65535))
    assert len(af.encode(ack)) == 10
    assert af.decode(af.encode(ack)) == ack


@pytest.mark.parametrize("mutate, message", [
    (lambda b: b[:4], "short"),
    (lambda b: b"\x00" + b[1:], "magic"),
    (lambda b: b[:20] + bytes([b[20] ^ 1]) + b[21:], "crc"),
])
def test_decode_rejects_bad_frames(mutate, message):
    with pytest.raises(af.FrameError, match=message):
        af.decode(mutate(bytes.fromhex(GOLDEN)))


def test_bridge_folds_siren_commands_into_mask():
    s = SirenState(["siren-01", "siren-02"])
    assert s.apply("siren-02", {"on": True, "level": "warning"}) == "S 2 2\n"
    assert s.apply("siren-01", {"on": True, "level": "critical"}) == "S 3 3\n"
    assert s.apply("siren-02", {"on": False, "level": "normal"}) == "S 1 3\n"
    assert s.apply("siren-99", {"on": True}) is None


def test_bridge_forwards_only_json_lines():
    assert route_line('{"v":1}\n') == b'{"v":1}'
    assert route_line("# beacon 4 frames=10\n") is None
    assert route_line("{garbled\n") is None


def test_tof_mm_uses_the_formerly_reserved_bytes():
    t = golden_telemetry()
    assert t.tof_mm == 0  # the golden vector predates the field: its two bytes were zero
    t.tof_mm = 517
    frame = af.Frame(af.TELEMETRY, af.site_hash("jharia-demo"), 7, 12, t)
    raw = af.encode(frame)
    assert len(raw) == len(bytes.fromhex(GOLDEN))
    assert af.decode(raw).body.tof_mm == 517
