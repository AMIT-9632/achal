# SPDX-License-Identifier: MIT
from datetime import UTC, datetime

from achal_common import Alert, Level
from alerts import TEMPLATES, render


def test_every_language_renders_every_level():
    for lang in TEMPLATES:
        for level in Level:
            a = Alert(id="x", site="jharia-demo", ts=datetime.now(UTC), level=level,
                      nodes=["n12"], reasons=["tilt_rate_high"])
            assert render(a, lang)


def test_hindi_critical_is_translated():
    a = Alert(id="x", site="s", ts=datetime.now(UTC), level=Level.CRITICAL, nodes=["n12"],
              reasons=["crack_opening"])
    assert "खतरा" in render(a, "hi")
