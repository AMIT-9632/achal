# SPDX-License-Identifier: MIT
import random

from achal_common import Telemetry, load_site
from field_sim import ground_state


def test_flat_ground_readings_validate():
    site = load_site("jharia-demo")
    rng = random.Random(1)
    for node in site.node_ids():
        Telemetry(site=site.site_id, node=node, seq=0, ts="2026-10-21T00:00:00Z", **ground_state(node, 0, rng))
