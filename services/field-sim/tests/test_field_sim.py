# SPDX-License-Identifier: MIT
import math
from collections import Counter
from datetime import datetime

import pytest
from achal_common import Telemetry, load_site
from achal_sim import SCENARIOS, FieldSimulator, simulate
from achal_sim.export import export_one, load_jsonl
from achal_sim.geomech import GaussianBowl, KnotheTrough, creep_to_failure, knothe_time, knothe_time_inverse
from achal_sim.scenarios import SUBSIDENCE_FACTOR

SITE = load_site("jharia-demo")
P = SITE.panel


def trough(width: float = 100.0) -> KnotheTrough:
    return KnotheTrough.over_panel(0, width, 0, width, depth_m=P.seam_depth_m, extraction_m=P.extraction_height_m,
                                   subsidence_factor=SUBSIDENCE_FACTOR, angle_of_draw_deg=P.angle_of_draw_deg)


# --- Knothe trough physics ---

def test_supercritical_max_subsidence_is_factor_times_extraction():
    wide = trough(1000)
    assert wide.subsidence(500, 500) == pytest.approx(SUBSIDENCE_FACTOR * P.extraction_height_m * 1000, rel=1e-6)


def test_subcritical_panel_subsides_less_and_peaks_at_centre():
    t = trough(100)
    centre = t.subsidence(50, 50)
    assert 0.5 * t.s_max_mm < centre < t.s_max_mm
    assert all(t.subsidence(x, y) <= centre for x in range(0, 101, 10) for y in range(0, 101, 10))


def test_trough_is_symmetric_and_tilt_antisymmetric():
    t = trough(100)
    for d in (5, 20, 45, 80):
        west, east = t.response(50 - d, 50), t.response(50 + d, 50)
        assert west.s_mm == pytest.approx(east.s_mm, rel=1e-9)
        assert west.tilt_x == pytest.approx(-east.tilt_x, rel=1e-6)
        assert west.strain_x == pytest.approx(east.strain_x, rel=1e-4)


def test_tilt_sign_convention():
    """tilt_x > 0 means the surface rises towards +x: so negative on the west flank of a trough."""
    t = trough(100)
    assert t.response(10, 50).tilt_x < 0
    assert t.response(90, 50).tilt_x > 0
    assert t.response(50, 10).tilt_y < 0
    assert abs(t.response(50, 50).tilt_x) < 1e-6


def test_max_slope_equals_smax_over_r_at_the_panel_edge():
    """Knothe: the steepest slope is S_max / r, at the inflection point (the edge when the offset is 0)."""
    wide = trough(1000)
    r = wide.response(0, 500)
    assert abs(r.tilt_x) == pytest.approx(wide.s_max_mm / wide.r_m, rel=1e-3)
    assert wide.r_m == pytest.approx(P.seam_depth_m * math.tan(math.radians(P.angle_of_draw_deg)))


def test_ground_moves_towards_centre_tension_outside_compression_inside():
    wide = trough(1000)
    r = wide.r_m
    assert wide.response(-0.3 * r, 500).u_x > 0      # west side moves east, towards the trough
    assert wide.response(-0.4 * r, 500).strain_x > 0  # tension beyond the edge: cracks open here
    assert wide.response(0.4 * r, 500).strain_x < 0   # compression over the goaf
    assert wide.response(500, 500).strain_x == pytest.approx(0, abs=1e-3)


def test_gaussian_bowl_tilts_towards_its_centre():
    bowl = GaussianBowl(50, 50, 100, 10)
    assert bowl.response(45, 50).tilt_x < 0 < bowl.response(55, 50).tilt_x
    assert bowl.subsidence(50, 50) == pytest.approx(100)


# --- time functions ---

def test_knothe_time_and_inverse():
    assert knothe_time(10, 10, 0.1) == 0
    assert knothe_time(10 + 1 / 0.1, 10, 0.1) == pytest.approx(1 - math.exp(-1))
    assert knothe_time(knothe_time_inverse(0.4, 10, 0.1), 10, 0.1) == pytest.approx(0.4)


def test_creep_has_linear_inverse_velocity_reaching_zero_at_failure():
    """Fukuzono (1985): 1/v falls linearly to zero at failure, which is what the risk engine extrapolates."""
    onset, fail, h = 10.0, 34.0, 1e-4
    times = [12.0, 18.0, 24.0, 30.0]
    inv_v = [2 * h / (creep_to_failure(t + h, onset, fail) - creep_to_failure(t - h, onset, fail)) for t in times]
    slope = (inv_v[-1] - inv_v[0]) / (times[-1] - times[0])
    assert inv_v[1] - inv_v[0] == pytest.approx(slope * 6, rel=1e-3)
    assert times[0] - inv_v[0] / slope == pytest.approx(fail, rel=1e-3)
    assert creep_to_failure(fail + 5, onset, fail) == pytest.approx(1.0)


# --- simulator ---

def test_every_scenario_emits_valid_telemetry():
    for scenario in SCENARIOS:
        rows = list(simulate(scenario, seed=7, hours=2, period_s=600))
        assert rows and len(rows) == 12 * 25
        for row in rows:
            Telemetry.model_validate(row)


def test_same_seed_same_run_different_seed_different_run():
    a = list(simulate("pillar_collapse", seed=3, hours=3, period_s=300))
    b = list(simulate("pillar_collapse", seed=3, hours=3, period_s=300))
    c = list(simulate("pillar_collapse", seed=4, hours=3, period_s=300))
    assert a == b
    assert a != c


def test_seeds_move_the_event():
    plans = [FieldSimulator(SITE, "pillar_collapse", s).plan for s in range(5)]
    assert len({(p.params["cx_m"], p.params["cy_m"]) for p in plans}) == 5
    assert all(p.onset_h < p.failure_h for p in plans)


def test_normal_ground_is_quiet_and_never_hazardous():
    rows = list(simulate("normal", seed=11, hours=48, period_s=300))
    assert not any(r["truth"]["hazard"] for r in rows)
    assert max(abs(r["tilt_x_deg"]) for r in rows) < 0.05
    assert max(abs(r["crack_mm"]) for r in rows) < 0.3
    assert {r["truth"]["event"] for r in rows} <= {"none", "blast"}
    assert all(3.5 < r["batt_v"] < 4.25 for r in rows)


def test_pillar_collapse_labels_and_signals():
    sim = FieldSimulator(SITE, "pillar_collapse", 42, period_s=300)
    plan = sim.plan
    rows = list(simulate("pillar_collapse", seed=42, period_s=300))
    hot = [r for r in rows if r["node"] in plan.pothole_nodes]
    assert len(plan.pothole_nodes) >= 2
    events = Counter(r["truth"]["event"] for r in hot)
    assert events["precursor"] > 0 and events["collapse"] > 0
    for r in hot:
        if r["truth"]["hazard"]:
            failure = r["truth"]["failure_ts"]
            assert r["truth"]["onset_ts"] < failure
            assert (r["truth"]["event"] == "collapse") == (r["ts"] >= failure)
    peak_after = max(r["vib_peak_g"] for r in hot if r["truth"]["event"] == "collapse")
    assert peak_after > 0.05
    assert max(r["truth"]["subsidence_mm"] for r in hot) > 300


def test_trough_failure_time_matches_damage_threshold():
    plan = FieldSimulator(SITE, "trough", 42).plan
    assert plan.onset_h < plan.failure_h < plan.duration_h
    assert 5 <= len(plan.trough_nodes) < 25


def test_faults_scenario_injects_each_fault_kind_without_hazard():
    rows = list(simulate("faults", seed=5, period_s=600))
    faults = {r["truth"].get("fault") for r in rows} - {None}
    assert faults == {"stuck", "spike", "drift", "battery"}
    assert not any(r["truth"]["hazard"] for r in rows)
    stuck = [r for r in rows if r["truth"].get("fault") == "stuck"]
    assert len({r["tilt_x_deg"] for r in stuck}) == 1


def test_fire_heats_hazard_nodes():
    rows = list(simulate("fire", seed=2, period_s=600))
    fire = [r for r in rows if r["truth"]["event"] == "fire"]
    assert fire and max(r["temp_c"] for r in fire) > 45
    assert all(r["truth"]["hazard"] for r in fire)


def test_export_round_trip(tmp_path):
    path = export_one("pillar_collapse", 1, 2.0, 2, 300, tmp_path, datetime.fromisoformat("2026-10-01T00:00:00+00:00"),
                      gz=True)
    rows = list(load_jsonl(path))
    assert len(rows) == 12 * 25
    assert (tmp_path / "pillar_collapse.manifest.json").exists()
