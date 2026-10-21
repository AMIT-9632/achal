# SPDX-License-Identifier: MIT
from datetime import UTC, datetime, timedelta

import pytest
from achal_common import Telemetry, load_site
from mesh import GATEWAY, PAYLOAD_BYTES, Mesh, RadioProfile, airtime_s, encoded_frame

T0 = datetime(2026, 10, 21, tzinfo=UTC)


def reading(site_id: str, node: str, seq: int, period: int = 60) -> Telemetry:
    return Telemetry(site=site_id, node=node, seq=seq, ts=T0 + timedelta(seconds=seq * period), tilt_x_deg=0.01,
                     tilt_y_deg=-0.02, vib_rms_g=0.001, vib_peak_g=0.004, crack_mm=0.3, ext_mm=-0.1, temp_c=29.5,
                     batt_v=3.91)


def run(mesh: Mesh, periods: int) -> list[Telemetry]:
    out = []
    for k in range(periods):
        for n in mesh.nodes:
            out += mesh.transmit(reading(mesh.site.site_id, n, k))
    return out


def test_airtime_matches_semtech_calculator():
    # Semtech LoRa calculator, BW 125 kHz, CR 4/5, 8-symbol preamble, explicit header, CRC on.
    assert airtime_s(31, 7) == pytest.approx(0.071936, abs=1e-6)
    assert airtime_s(31, 12) == pytest.approx(1.810432, abs=1e-6)  # low data rate optimisation on
    assert airtime_s(31, 9) > airtime_s(31, 8) > airtime_s(31, 7)


def test_frame_fits_smallest_in865_payload():
    assert len(encoded_frame(reading("jharia-demo", "n07", 1))) == PAYLOAD_BYTES <= 51


def test_path_loss_grows_with_distance():
    r = RadioProfile()
    assert r.path_loss_db(10) < r.path_loss_db(100) < r.path_loss_db(500)
    assert r.path_loss_db(100) - r.path_loss_db(10) == pytest.approx(10 * r.path_loss_exponent)


def test_frames_carry_routing_metadata():
    site = load_site("jharia-colliery")
    frames = run(Mesh(site, seed=3), 3)
    assert frames
    for f in frames:
        assert f.path[0] == f.node and f.hops == len(f.path) <= site.mesh.max_hops
        assert f.rssi_dbm is not None and f.snr_db is not None
        assert f.neighbour_rssi and f.node not in f.neighbour_rssi
    assert any(f.hops > 1 for f in frames), "far nodes should relay at colliery scale"


def test_deterministic_for_a_seed():
    site = load_site("jharia-colliery")
    a = run(Mesh(site, seed=7, fail_fraction=0.2, fail_at_s=60), 5)
    b = run(Mesh(site, seed=7, fail_fraction=0.2, fail_at_s=60), 5)
    assert [(f.node, f.seq, f.path) for f in a] == [(f.node, f.seq, f.path) for f in b]


def test_dead_nodes_go_silent_and_live_ones_get_through():
    site = load_site("jharia-colliery")
    mesh = Mesh(site, seed=11, fail_fraction=0.3, fail_at_s=120)
    frames = run(mesh, 30)
    late = {f.node for f in frames if f.seq >= 2}
    assert late.isdisjoint(mesh.failing)
    assert mesh.stats.pdr >= 0.95
    assert mesh.stats.dead_node_readings == 28 * len(mesh.failing)


def test_route_repair_after_relay_failure():
    site = load_site("jharia-colliery")
    probe = Mesh(site, seed=5)
    relays = {p for p in probe.parent.values() if p not in (None, GATEWAY)}
    assert relays
    mesh = Mesh(site, seed=5, fail_fraction=0.0)
    mesh.failing, mesh.fail_at_s = relays, 60  # kill every relay without warning
    run(mesh, 10)  # well before the dead relays are dropped from routing tables
    orphans = [n for n, p in probe.parent.items() if p in relays]
    assert mesh.stats.local_repairs >= len(orphans) > 0
    assert mesh.stats.pdr >= 0.95


def test_mesh_beats_single_hop_at_colliery_scale():
    site = load_site("jharia-colliery")
    harsh = RadioProfile(path_loss_exponent=4.4, shadowing_sigma_db=9.0)
    mesh, star = Mesh(site, harsh, seed=2), Mesh(site, harsh, seed=2, mode="star")
    run(mesh, 10)
    run(star, 10)
    assert mesh.stats.pdr > star.stats.pdr
    assert mesh.stats.airtime_s < star.stats.airtime_s


def test_lost_acks_make_duplicates_with_the_same_seq():
    site = load_site("jharia-colliery")
    frames = run(Mesh(site, seed=1), 20)
    keys = [(f.node, f.seq) for f in frames]
    assert len(keys) > len(set(keys))
