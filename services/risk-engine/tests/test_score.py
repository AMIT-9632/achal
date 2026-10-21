# SPDX-License-Identifier: MIT
from datetime import UTC, datetime

from achal_common import Level, Telemetry
from risk_engine import level_for, score


def reading(**kw):
    base = dict(site="jharia-demo", node="n00", seq=0, ts=datetime.now(UTC),
                tilt_x_deg=0.0, tilt_y_deg=0.0, vib_rms_g=0.0005, crack_mm=0.0, ext_mm=0.0, batt_v=3.9)
    return Telemetry(**(base | kw))


def test_quiet_ground_is_normal():
    s, reasons = score(reading())
    assert level_for(s) is Level.NORMAL and reasons == []


def test_large_tilt_and_crack_is_critical():
    s, reasons = score(reading(tilt_x_deg=0.2, crack_mm=3))
    assert level_for(s) is Level.CRITICAL
    assert {"tilt_rate_high", "crack_opening"} <= set(reasons)
