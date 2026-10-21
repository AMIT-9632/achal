# SPDX-License-Identifier: MIT
import costing
import power_budget as pb
import pytest


def test_airtime_matches_firmware_and_semtech_calculator():
    # Same reference values as test_slots_and_airtime in firmware/test/test_native.
    assert pb.lora_airtime_s(51) * 1000 == pytest.approx(102.7, abs=0.1)
    assert pb.lora_airtime_s(51, sf=12) * 1000 == pytest.approx(2465.8, abs=0.1)


def test_relay_costs_more_than_leaf_and_longer_period_saves_energy():
    leaf, relay = pb.period_budget("leaf", 60), pb.period_budget("relay", 60)
    assert relay.avg_ma > leaf.avg_ma
    assert pb.period_budget("relay", 300).avg_ma < relay.avg_ma
    assert pb.period_budget("relay", 60, prototype=False).avg_ma < relay.avg_ma


def test_solar_covers_worst_case_node():
    assert pb.solar_mah_per_day() > pb.period_budget("relay", 60).mah_per_day


def test_bom_rows_are_complete():
    lines = costing.load()
    assert {x.kit for x in lines} == {"node", "gateway", "siren"}
    for x in lines:
        assert x.qty > 0 and x.unit_inr > 0 and x.gst in {"ex", "incl"}
        assert x.basis in {"quote", "estimate"}
        assert x.basis == "estimate" or x.source.startswith("https://"), x.item


def test_site_cost_arithmetic():
    lines = [costing.Line("node", "a", 2, 100, "ex", "quote", "https://x"),
             costing.Line("gateway", "b", 1, 1000, "incl", "estimate", ""),
             costing.Line("siren", "c", 1, 500, "incl", "quote", "https://y")]
    sc = costing.site_cost(lines, nodes=25, sirens=2, area_m2=20_000, people=100)
    assert sc.node == pytest.approx(236)
    assert sc.total == pytest.approx(25 * 236 + 1000 + 2 * 500)
    assert sc.per_ha == pytest.approx(sc.total / 2)
    assert costing.kit_cost(lines, "gateway") == (1000, 1000)


def test_indian_grouping():
    assert costing.rupees(113271) == "₹1,13,271"
    assert costing.rupees(4850) == "₹4,850"
