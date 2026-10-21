# SPDX-License-Identifier: MIT
import json
from datetime import UTC, datetime
from pathlib import Path

from achal_common import Level, Telemetry, load_site, topics

ROOT = Path(__file__).resolve().parents[3]


def test_site_loads_and_grid_fits_panel():
    site = load_site("jharia-demo")
    ids = site.node_ids()
    assert len(ids) == 25
    corner = site.node_position_m(ids[-1])
    assert 0 <= corner.x <= site.panel.width_m
    assert 0 <= corner.y <= site.panel.length_m


def test_telemetry_matches_schema_required_fields():
    schema = json.loads((ROOT / "schemas" / "telemetry.v1.json").read_text())
    model_fields = set(Telemetry.model_fields)
    assert set(schema["required"]) <= model_fields
    assert set(schema["properties"]) == model_fields


def test_telemetry_round_trip():
    t = Telemetry(site="jharia-demo", node="n07", seq=1, ts=datetime.now(UTC),
                  tilt_x_deg=0.01, tilt_y_deg=-0.02, vib_rms_g=0.001, crack_mm=0, ext_mm=0, batt_v=3.9)
    assert Telemetry.model_validate_json(t.model_dump_json()) == t


def test_levels_ordered():
    assert Level.CRITICAL.rank > Level.WARNING.rank > Level.WATCH.rank > Level.NORMAL.rank


def test_topics():
    assert topics.telemetry("jharia-demo", "n01") == "achal/jharia-demo/telemetry/n01"
