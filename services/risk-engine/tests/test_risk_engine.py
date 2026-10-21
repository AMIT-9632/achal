# SPDX-License-Identifier: MIT
"""Unit tests for the layered risk engine on small synthetic (SYNTHETIC) signals."""

import math

import numpy as np
import pytest
from achal_common import Alert, Level, RiskAssessment, RiskGrid, load_site
from achal_risk import AnomalyModel, EngineConfig, RiskEngine
from achal_risk.data import from_records
from achal_risk.grid import idw_grid
from achal_risk.kpi import replay, score_run
from achal_risk.rules import BOUNDS, ramp
from achal_risk.store import CHANNELS, CR, EX, TP, TX, TY, VB, Store
from achal_risk.surrogate import generate
from achal_risk.ttf import inverse_velocity_ttf, trend_score
from risk_engine import to_messages

SITE = load_site("jharia-demo")
PERIOD = SITE.mesh.sample_period_s
N = len(SITE.node_ids())
T0 = 1_767_225_600.0


def quiet(hours: float, seed: int = 0) -> np.ndarray:
    """Flat ground: sensor noise, a diurnal temperature cycle and a little thermal coupling."""
    rng = np.random.default_rng(seed)
    k = int(hours * 3600 / PERIOD)
    t = np.arange(k) * PERIOD / 86400
    x = np.zeros((k, N, len(CHANNELS)))
    temp = 27 + 5 * np.sin(2 * np.pi * t)[:, None] + rng.normal(0, 0.2, (k, N))
    x[:, :, TP] = temp
    x[:, :, TX] = rng.normal(0, 0.002, (k, N)) + 0.0005 * (temp - 27)
    x[:, :, TY] = rng.normal(0, 0.002, (k, N))
    x[:, :, CR] = rng.normal(0, 0.01, (k, N))
    x[:, :, EX] = rng.normal(0, 0.03, (k, N)) + 0.01 * (temp - 27)
    x[:, :, VB] = np.abs(rng.normal(4e-4, 1e-4, (k, N)))
    x[:, :, -1] = 3.9
    return x


def run_engine(x: np.ndarray, cfg: EngineConfig | None = None, model=None):
    eng = RiskEngine.for_site(SITE, model=model, cfg=cfg)
    results = []
    for k in range(len(x)):
        eng.ingest_row(T0 + k * PERIOD, x[k])
        r = eng.maybe_evaluate(T0 + k * PERIOD)
        if r:
            results.append(r)
    return eng, results


def block(node: str) -> list[int]:
    """Indices of a node and its 8 neighbours on the 5 x 5 grid."""
    r, c = divmod(int(node[1:]), 5)
    return [rr * 5 + cc for rr in range(r - 1, r + 2) for cc in range(c - 1, c + 2) if 0 <= rr < 5 and 0 <= cc < 5]


# --- rules ---

def test_ramp_lands_thresholds_on_level_boundaries():
    s = ramp(np.array([0.0, 1.0, 2.0, 3.0, 6.0, 99.0]), 1.0, 2.0, 3.0)
    assert list(s) == pytest.approx([0.0, *BOUNDS, 1.0, 1.0])


# --- whole engine on synthetic ground ---

def test_quiet_ground_stays_normal():
    _, res = run_engine(quiet(30))
    assert max(r.site_level for r in res) == 0
    assert all(r.alert is None for r in res)


def test_movement_shared_by_neighbours_warns():
    x = quiet(30, seed=1)
    k0 = int(20 * 3600 / PERIOD)
    days = (np.arange(len(x) - k0) * PERIOD / 86400)[:, None]
    nodes = block("n12")
    x[k0:, nodes, TX] += 0.4 * days          # 0.4 deg/day of tilt over a 3 x 3 block
    x[k0:, nodes, EX] += 8.0 * days          # and stretching extensometers
    _, res = run_engine(x)
    last = res[-1]
    assert last.levels[12] >= 2
    n12 = last.nodes[12]
    assert {"tilt_rate_high", "neighbour_consensus"} <= set(n12.reasons)
    assert n12.agreeing_neighbours >= 2
    assert any(r.alert and r.alert["siren"] for r in res)


def test_lone_node_drift_is_capped_and_marked_suspect():
    x = quiet(30, seed=2)
    k0 = int(12 * 3600 / PERIOD)
    x[k0:, 6, TY] += 0.5 * (np.arange(len(x) - k0) * PERIOD / 86400)  # one tilt sensor creeping alone
    _, res = run_engine(x)
    assert max(r.levels[6] for r in res) <= 1
    assert "sensor_fault_suspected" in res[-1].nodes[6].reasons


def test_stuck_sensor_is_flagged_and_cannot_alarm():
    x = quiet(12, seed=3)
    k0 = int(6 * 3600 / PERIOD)
    x[k0:, 3, [TX, TY, CR, EX]] = x[k0, 3, [TX, TY, CR, EX]]
    _, res = run_engine(x)
    assert res[-1].faulty[3]
    assert "sensor_fault_suspected" in res[-1].nodes[3].reasons
    assert res[-1].levels[3] == 0


def test_hysteresis_holds_level_through_brief_dips():
    eng = RiskEngine.for_site(SITE)
    eng.level[:] = 2
    for _ in range(5):  # well below Warning for less than confirm_down ticks
        eng._apply_hysteresis(np.zeros(N, int), np.full(N, 0.1))
    assert (eng.level == 2).all()
    for _ in range(eng.cfg.confirm_down):
        eng._apply_hysteresis(np.zeros(N, int), np.full(N, 0.1))
    assert (eng.level == 1).all()  # steps down one level at a time


def test_silent_node_is_reported():
    x = quiet(10, seed=4)
    x[int(8 * 3600 / PERIOD):, 20] = np.nan
    _, res = run_engine(x)
    assert "node_silent" in res[-1].nodes[20].reasons


# --- time to failure ---

def test_inverse_velocity_recovers_failure_time():
    rng = np.random.default_rng(5)
    k = 1440
    t = (np.arange(k) - (k - 1)) / 1440                     # last 24 h, days, now = 0
    tf_true = 10 / 24                                        # failure 10 h from now
    onset = -20 / 24
    tf_abs_from_onset = tf_true - onset
    d = np.where(t >= onset, 2.0 * np.log(tf_abs_from_onset / (tf_true - t)), 0.0)
    y = d[:, None] + rng.normal(0, 0.03, (k, 1))
    ttf, _ = inverse_velocity_ttf(t, y, np.array([0.03]))
    assert ttf[0] == pytest.approx(10.0, rel=0.25)


def test_constant_velocity_has_no_failure_time():
    rng = np.random.default_rng(6)
    k = 1440
    t = (np.arange(k) - (k - 1)) / 1440
    y = (np.maximum(t + 8 / 24, 0) * 5.0)[:, None] + rng.normal(0, 0.03, (k, 1))  # creep starts 8 h ago, steady
    ttf, _ = inverse_velocity_ttf(t, y, np.array([0.03]))
    assert np.isnan(ttf[0])


def test_trend_score_orders_by_urgency():
    s = trend_score(np.array([1.0, 6.0, 24.0, 72.0, 500.0, np.nan]))
    assert list(np.diff(s[:5]) <= 0) == [True] * 4
    assert s[1] == pytest.approx(0.85) and s[2] == pytest.approx(0.6) and s[5] == 0.0


# --- anomaly model, grid, store, data ---

def test_isolation_forest_scores_outliers_above_watch():
    rng = np.random.default_rng(7)
    x = rng.normal(0, 1, (3000, 5))
    m = AnomalyModel.fit(x)
    assert np.percentile(m.score(x), 50) < 0.1
    assert m.score(np.array([[8.0, 8, 8, 8, 8]]))[0] > BOUNDS[0]


def test_idw_grid_matches_nodes_and_shape():
    xy = np.array([[0.0, 0.0], [10.0, 0.0]])
    g = idw_grid(xy, np.array([0.2, 0.8]), 0, 0, 5, 5, 1, 3)
    assert g.shape == (1, 3)
    assert g[0, 0] == pytest.approx(0.2, abs=1e-3) and g[0, 2] == pytest.approx(0.8, abs=1e-3)
    assert g[0, 1] == pytest.approx(0.5)


def test_store_slots_late_packets_by_timestamp():
    s = Store(2, 60, 600)
    s.put(0, 600, np.ones(len(CHANNELS)))
    s.put(1, 540, np.full(len(CHANNELS), 2.0))      # arrives late, belongs one row back
    w = s.window(2)
    assert w[1, 0, 0] == 1 and w[0, 1, 0] == 2 and np.isnan(w[1, 1, 0])
    assert not s.put(0, -60, np.ones(len(CHANNELS)))  # older than the window: dropped


def test_records_with_w1_truth_contract():
    recs = []
    for k in range(3):
        for node, hz in (("n00", k >= 1), ("n01", False)):
            recs.append({"node": node, "ts": f"2026-10-01T00:0{k}:00Z", "tilt_x_deg": 0.0, "tilt_y_deg": 0.0,
                         "crack_mm": 0.0, "ext_mm": 0.0, "vib_rms_g": 4e-4, "temp_c": 25, "batt_v": 3.9,
                         "truth": {"hazard": hz, "event": "precursor" if hz else "none",
                                   **({"onset_ts": "2026-10-01T00:00:30Z", "failure_ts": "2026-10-01T05:00:00Z"}
                                      if hz else {})}})
    run = from_records(recs, SITE.node_ids(), PERIOD)
    assert run.x.shape == (3, N, len(CHANNELS))
    assert run.hazard_nodes[0] and not run.hazard_nodes[1]
    assert run.failure_s - run.first_onset_s == pytest.approx(5 * 3600 - 30)


# --- harness and service glue ---

def test_harness_scores_a_surrogate_collapse():
    run = generate(SITE, "pillar_collapse", seed=3, days=4)
    out = replay(run, SITE, EngineConfig(), None, None)
    res = score_run(run, out, SITE.mesh.spacing_m)
    assert res["detected_warning"] and res["false_warnings"] == 0


def test_service_messages_validate_against_contracts():
    x = quiet(8, seed=8)
    eng, res = run_engine(x)
    risks, grid, alert = to_messages(SITE, eng, res[-1], Level.NORMAL)
    assert len(risks) == N and all(isinstance(r, RiskAssessment) for r in risks)
    assert isinstance(grid, RiskGrid) and len(grid.values) == grid.rows * grid.cols
    assert alert is None or isinstance(alert, Alert)
    assert math.isclose(grid.x0_m, -40)


# --- integration with W1's field simulator ---

def test_w1_pillar_collapse_warns_before_failure():
    from achal_risk.kpi import commission_window, make_run

    run = make_run("sim", SITE, "pillar_collapse", 42, hours=48)
    out = replay(run, SITE, EngineConfig(), None, None, commission_window("sim", SITE, 42))
    res = score_run(run, out, SITE.mesh.spacing_m)
    assert res["detected_warning"] and res["lead_h_warning"] >= 6
    assert res["false_warnings"] == 0
