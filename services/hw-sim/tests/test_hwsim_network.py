# SPDX-License-Identifier: MIT
import math

from achal_hwsim import e2e, network, scenarios
from achal_hwsim.components import esp32


def test_lorawan_matches_pure_aloha():
    xy = scenarios.grid(20, 10.0)
    for interval in (1.0, 10.0):
        # capture switched off so the simulator is pure ALOHA: S/G = e^(-2G) per channel
        # one seed is one draw of the nodes' fixed phases, so average several
        pdr = [
            network.metrics(
                network.simulate("lorawan", xy, interval, 300.0, seed=s, lora_kw={"capture_db": None}),
                20,
                interval,
                300.0,
            )["pdr"]
            for s in range(1, 9)
        ]
        g = 20 * esp32.lora_airtime_s(32 + 13, 7) / interval / 3
        assert abs(sum(pdr) / len(pdr) - math.exp(-2 * g)) < 0.04


def test_wifi_mesh_delivers_and_goes_multihop_when_sparse():
    dense = network.metrics(network.simulate("wifi_mesh", scenarios.grid(20, 10.0), 1.0, 60.0, 1), 20, 1.0, 60.0)
    assert dense["pdr"] > 0.99 and dense["max_hops"] == 1
    sparse = network.metrics(network.simulate("wifi_mesh", scenarios.grid(100, 40.0), 5.0, 60.0, 1), 100, 5.0, 60.0)
    assert sparse["max_hops"] > 1


def test_end_to_end_smoke_detects_the_critical_scenario():
    m = e2e.run("critical", "wifi_mesh", n=6, duration_s=400, seed=1)
    assert m["detection"]["per_level"]["2"]["recall"] == 1.0
    assert m["network"]["sent"] > 0
