# SPDX-License-Identifier: MIT
"""The simulator calls the firmware's own achal_dsp C code, so these also guard the node."""

import math

import numpy as np
from achal_hwsim import dsp


def test_vibration_features_match_closed_form():
    fs, f, amp = 200.0, 10.0, 0.02
    t = np.arange(200) / fs
    v = dsp.vib_features(amp * np.sin(2 * math.pi * f * t + 0.4), fs)
    ppv = amp * 9806.65 / (2 * math.pi * f)
    assert abs(v.ppv_mm_s - ppv) < 0.08 * ppv
    assert abs(v.dom_hz - f) < 1.0
    assert dsp.dgms_ppv_limit(v.dom_hz) == 10.0


def test_tof_filter_rejects_invalid_shots():
    cfg = dsp.tof_cfg()
    good = [(500, 0, 2.0, 3.0), (502, 0, 2.0, 3.0), (501, 0, 2.0, 3.0)]
    bad = [(8190, 4, 0.0, 0.0), (120, 0, 0.1, 3.0), (498, 0, 2.0, 40.0)]
    mm, n_valid = dsp.tof_filter(cfg, good + bad)
    assert n_valid == 3 and mm == 501.0
    mm, n_valid = dsp.tof_filter(cfg, bad)
    assert mm is None and n_valid == 0


def test_detector_needs_persistence_and_caps_vibration_at_warning():
    d = dsp.Detector()
    assert [d.step(0.15, None, 0, 0) for _ in range(3)] == [0, 0, 1]
    d = dsp.Detector()
    for _ in range(20):
        d.step(0.0, None, 50.0, 5.0)
    assert d.level == 1


def test_tilt_from_gravity_vector():
    tx, ty = dsp.tilt_deg(math.sin(math.radians(0.5)), 0.0, math.cos(math.radians(0.5)))
    assert abs(tx - 0.5) < 1e-3 and abs(ty) < 1e-5
