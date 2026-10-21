# SPDX-License-Identifier: MIT
"""AI risk engine.

SCAFFOLD STUB (W0). Scores each reading with fixed thresholds and publishes a
RiskAssessment per node, plus an Alert whenever the site level changes.

W3 replaces `score` with: rules -> Isolation Forest -> multi-node consensus ->
fused score, inverse-velocity time-to-failure, kriged risk grid, LSTM later,
and the KPI harness that scores runs against simulator ground truth.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from achal_common import Alert, Level, RiskAssessment, Telemetry, load_site, topics
from achal_common.mqtt import connect

log = logging.getLogger("risk-engine")

# Placeholder thresholds; W3 calibrates them on simulated Jharia runs.
THRESHOLDS = {"tilt_deg": 0.05, "crack_mm": 1.0, "vib_rms_g": 0.02}


def score(t: Telemetry) -> tuple[float, list[str]]:
    reasons = []
    tilt = max(abs(t.tilt_x_deg), abs(t.tilt_y_deg))
    parts = [
        tilt / THRESHOLDS["tilt_deg"],
        abs(t.crack_mm) / THRESHOLDS["crack_mm"],
        t.vib_rms_g / THRESHOLDS["vib_rms_g"],
    ]
    if parts[0] >= 1:
        reasons.append("tilt_rate_high")
    if parts[1] >= 1:
        reasons.append("crack_opening")
    if parts[2] >= 1:
        reasons.append("vibration_burst")
    return min(1.0, max(parts) / 2), reasons


def level_for(s: float) -> Level:
    if s >= 0.85:
        return Level.CRITICAL
    if s >= 0.6:
        return Level.WARNING
    if s >= 0.35:
        return Level.WATCH
    return Level.NORMAL


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    site = load_site()
    node_levels: dict[str, Level] = {}
    state = {"site_level": Level.NORMAL}

    def on_message(_topic: str, payload: bytes) -> None:
        t = Telemetry.model_validate_json(payload)
        s, reasons = score(t)
        lvl = level_for(s)
        node_levels[t.node] = lvl
        ra = RiskAssessment(site=t.site, node=t.node, ts=t.ts, score=s, level=lvl, reasons=reasons,
                            components={"rules": s})
        client.publish(topics.risk(site.site_id, t.node), ra.model_dump_json(exclude_none=True), qos=1)

        site_level = max(node_levels.values(), key=lambda lv: lv.rank)
        if site_level != state["site_level"]:
            hot = [n for n, lv in node_levels.items() if lv == site_level]
            alert = Alert(id=str(uuid.uuid4()), site=site.site_id, ts=datetime.now(UTC), level=site_level,
                          previous_level=state["site_level"], nodes=hot, reasons=reasons or ["level_change"],
                          siren=site_level.rank >= Level.WARNING.rank)
            client.publish(topics.alert(site.site_id), alert.model_dump_json(exclude_none=True), qos=1)
            log.info("site level %s -> %s (%s)", state["site_level"].value, site_level.value, ",".join(hot))
            state["site_level"] = site_level

    client = connect("risk-engine", on_message, [topics.telemetry(site.site_id)])
    client.loop_forever()


if __name__ == "__main__":
    main()
