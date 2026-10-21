# SPDX-License-Identifier: MIT
"""AI risk engine service.

Subscribes to node telemetry, feeds the layered engine in `achal_risk`, and every
evaluation tick (sensor time) publishes per-node RiskAssessments, the IDW risk grid,
an Alert when the site level changes, and siren commands at Warning and above.

The Isolation Forest is trained at start-up on normal-scenario runs from the field
simulator when it is importable (dev checkout, or the compose mount); otherwise the
engine runs rules + consensus + trend only and says so in the log.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime

from achal_common import Alert, Level, RiskAssessment, RiskGrid, Telemetry, load_site, topics
from achal_common.models import LatLon
from achal_common.mqtt import connect
from achal_risk import EngineConfig, RiskEngine
from achal_risk.engine import EvalResult

log = logging.getLogger("risk-engine")


def build_engine(site) -> RiskEngine:
    model = rules = None
    if os.environ.get("RISK_TRAIN", "1") != "0":
        try:
            from achal_risk.kpi import train

            seeds = tuple(int(s) for s in os.environ.get("RISK_TRAIN_SEEDS", "1001,1002").split(","))
            model, rules = train("sim", site, seeds=seeds, hours=float(os.environ.get("RISK_TRAIN_HOURS", "48")),
                                 log=log.info)
            log.info("isolation forest trained on %d rows", model.train_rows)
        except ImportError:
            log.warning("field simulator not importable: running without the Isolation Forest layer")
    cfg = EngineConfig(eval_period_s=float(os.environ.get("RISK_EVAL_PERIOD_S", "600")))
    eng = RiskEngine.for_site(site, model=model, rules=rules, cfg=cfg)
    # Install-day calibration: a day of quiet data from the same sensors gives the thermal
    # coefficients. In the demo the simulator recreates that day for the running seed.
    seed = os.environ.get("RISK_COMMISSION_SEED", os.environ.get("SIM_SEED"))
    if seed is not None and model is not None:
        from achal_risk.kpi import commission_window

        eng.commission(commission_window("sim", site, int(seed)))
        log.info("commissioned thermal coefficients from a quiet day (seed %s)", seed)
    return eng


def to_messages(site, eng: RiskEngine, res: EvalResult, prev_level: Level) -> tuple[list, RiskGrid, Alert | None]:
    ts = datetime.fromtimestamp(res.ts_s, UTC)
    risks = [RiskAssessment(site=site.site_id, node=n.node, ts=ts, score=n.score, level=Level(n.level),
                            ttf_hours=n.ttf_hours, components=n.components, reasons=n.reasons,
                            agreeing_neighbours=n.agreeing_neighbours) for n in res.nodes]
    grid = RiskGrid(site=site.site_id, ts=ts, **eng.grid(res.scores))
    alert = None
    if res.alert:
        a = res.alert
        centroid = None
        if a["centroid_m"]:
            from achal_common.config import Point

            lat, lon = site.to_latlon(Point(x=a["centroid_m"][0], y=a["centroid_m"][1]))
            centroid = LatLon(lat=lat, lon=lon)
        alert = Alert(id=str(uuid.uuid4()), site=site.site_id, ts=ts, level=Level(a["level"]),
                      previous_level=prev_level, nodes=a["nodes"], centroid=centroid, ttf_hours=a["ttf_hours"],
                      reasons=a["reasons"], siren=a["siren"])
    return risks, grid, alert


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    site = load_site()
    eng = build_engine(site)
    state = {"level": Level.NORMAL}

    def on_message(_topic: str, payload: bytes) -> None:
        try:
            t = Telemetry.model_validate_json(payload)
        except ValueError as exc:
            log.warning("bad telemetry dropped: %s", exc)
            return
        reading = t.model_dump(exclude={"truth"})  # the engine never sees ground truth
        ts_s = t.ts.timestamp()
        eng.ingest(t.node, ts_s, reading)
        res = eng.maybe_evaluate(ts_s)
        if res is None:
            return
        risks, grid, alert = to_messages(site, eng, res, state["level"])
        for ra in risks:
            client.publish(topics.risk(site.site_id, ra.node), ra.model_dump_json(exclude_none=True), qos=1)
        client.publish(topics.risk_grid(site.site_id), grid.model_dump_json(), qos=0, retain=True)
        if alert:
            client.publish(topics.alert(site.site_id), alert.model_dump_json(exclude_none=True), qos=1)
            on = alert.level.rank >= Level.WARNING.rank
            if on or state["level"].rank >= Level.WARNING.rank:
                cmd = json.dumps({"on": on, "level": alert.level.value,
                                  "ts": alert.ts.isoformat().replace("+00:00", "Z")})
                for s in site.sirens:
                    client.publish(topics.siren_cmd(site.site_id, s.id), cmd, qos=1)
            log.info("site level %s -> %s nodes=%s reasons=%s ttf=%s", state["level"].value, alert.level.value,
                     ",".join(alert.nodes), ",".join(alert.reasons), alert.ttf_hours)
            state["level"] = alert.level

    client = connect("risk-engine", on_message, [topics.telemetry(site.site_id)])
    client.loop_forever()


if __name__ == "__main__":
    main()
