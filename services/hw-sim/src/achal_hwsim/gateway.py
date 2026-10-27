# SPDX-License-Identifier: MIT
"""Raspberry Pi gateway prototype: aggregation, node health and alarm decisions.

It consumes delivered packets in arrival order (from the network simulation,
from MQTT, or from the Wokwi serial bridge) and keeps, per node: last seen,
link quality, battery, sensor flags and the node's own alarm level.

Decisions (illustrative, to be validated in the field):
* GROUND WARNING / CRITICAL: a node reports level >= 1 / 2 with a ground cause
  (tilt or displacement). Raised at once (the node already applied persistence);
  corroboration by a neighbour within `corroborate_m` is recorded alongside.
* VIBRATION ADVISORY: a node's level comes from vibration only (PPV above the
  DGMS limit) - reported, but never as a subsidence alarm.
* HEALTH: node offline (no packet for `offline_after` reporting intervals),
  low battery (flag from the node), sensor faults, gateway outage (seen as a
  gap followed by buffered packets).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

CAUSE_TILT, CAUSE_DISP, CAUSE_VIB = 1, 2, 4
F_TILT_SENSOR, F_TOF, F_LOW_BATT, F_VIB_EXCEED = 1, 2, 4, 64
LEVEL_NAMES = {0: "normal", 1: "warning", 2: "critical"}


@dataclass
class NodeState:
    name: str
    xy: tuple[float, float]
    last_seen: float = -1.0
    last_seq: int = -1
    level: int = 0
    cause: int = 0
    batt_mv: int = 0
    soc: float = float("nan")
    rssi_dbm: float | None = None
    hops: int = 0
    flags: int = 0
    received: int = 0
    duplicates: int = 0
    online: bool = True
    tilt_deg: float = 0.0
    disp_mm: float | None = None
    ppv_mm_s: float = 0.0


@dataclass
class Event:
    t: float
    # ground_warning | ground_critical | vibration_advisory | node_offline | node_online | low_battery
    # | sensor_fault | cleared
    kind: str
    node: str
    detail: str = ""
    corroborated: bool | None = None
    t_sample: float | None = None  # when the triggering reading was taken (true time, for latency)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Gateway:
    names: list[str]
    xy: np.ndarray
    interval_s: float
    offline_after: float = 5.0
    corroborate_m: float = 15.0
    nodes: dict[str, NodeState] = field(init=False)
    events: list[Event] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.nodes = {n: NodeState(n, (float(x), float(y))) for n, (x, y) in zip(self.names, self.xy, strict=False)}

    def _neighbours(self, name: str) -> list[NodeState]:
        me = self.nodes[name]
        return [
            s
            for s in self.nodes.values()
            if s.name != name and np.hypot(s.xy[0] - me.xy[0], s.xy[1] - me.xy[1]) <= self.corroborate_m
        ]

    def ingest(self, pkt: dict, t_rx: float, rssi_dbm: float | None = None, hops: int = 0) -> list[Event]:
        st = self.nodes[pkt["node"]]
        new: list[Event] = []
        if pkt["seq"] <= st.last_seq and st.last_seq - pkt["seq"] < 30000:
            st.duplicates += 1
            return new
        if not st.online:
            st.online = True
            new.append(Event(t_rx, "node_online", st.name))
        st.last_seen, st.last_seq, st.received = t_rx, pkt["seq"], st.received + 1
        st.rssi_dbm, st.hops, st.batt_mv, st.soc = rssi_dbm, hops, pkt["batt_mv"], pkt.get("soc", float("nan"))
        st.tilt_deg = float(np.hypot(pkt["tilt_x_deg"], pkt["tilt_y_deg"]))
        st.disp_mm, st.ppv_mm_s = pkt.get("disp_mm"), pkt.get("ppv_mm_s") or 0.0
        if pkt["flags"] & F_LOW_BATT and not st.flags & F_LOW_BATT:
            new.append(Event(t_rx, "low_battery", st.name, f"{pkt['batt_mv']} mV"))
        if pkt["flags"] & F_TILT_SENSOR and not st.flags & F_TILT_SENSOR:
            new.append(Event(t_rx, "sensor_fault", st.name, "accelerometer window incomplete"))
        if pkt["flags"] & F_VIB_EXCEED and not st.flags & F_VIB_EXCEED:
            new.append(
                Event(
                    t_rx,
                    "vibration_advisory",
                    st.name,
                    f"PPV {st.ppv_mm_s:.1f} mm/s at {pkt.get('dom_hz') or 0:.0f} Hz above DGMS limit",
                    None,
                    pkt.get("t_true"),
                )
            )
        st.flags = pkt["flags"]
        lvl, cause = pkt["level"], pkt["cause"]
        ground = bool(cause & (CAUSE_TILT | CAUSE_DISP))
        if lvl > st.level or (lvl == st.level and lvl > 0 and ground and not st.cause & (CAUSE_TILT | CAUSE_DISP)):
            if ground:
                corr = any(n.level >= 1 and n.cause & (CAUSE_TILT | CAUSE_DISP) for n in self._neighbours(st.name))
                kind = "ground_critical" if lvl == 2 else "ground_warning"
                why = "+".join(
                    w
                    for b, w in ((CAUSE_TILT, "tilt"), (CAUSE_DISP, "displacement"), (CAUSE_VIB, "vibration"))
                    if cause & b
                )
                new.append(Event(t_rx, kind, st.name, why, corr, pkt.get("t_true")))
        elif lvl < st.level and lvl == 0:
            new.append(Event(t_rx, "cleared", st.name))
        st.level, st.cause = lvl, cause
        self.events += new
        return new

    def tick(self, t: float) -> list[Event]:
        """Call regularly: marks nodes offline when they have been silent too long."""
        new = []
        for st in self.nodes.values():
            if st.online and st.last_seen >= 0 and t - st.last_seen > self.offline_after * self.interval_s:
                st.online = False
                new.append(Event(t, "node_offline", st.name, f"silent {t - st.last_seen:.0f} s"))
        self.events += new
        return new

    def snapshot(self, t: float) -> list[dict]:
        return [
            {
                "t": t,
                "node": s.name,
                "x": s.xy[0],
                "y": s.xy[1],
                "online": s.online,
                "level": s.level,
                "cause": s.cause,
                "last_seen_age_s": (t - s.last_seen) if s.last_seen >= 0 else None,
                "batt_mv": s.batt_mv,
                "soc": s.soc,
                "rssi_dbm": s.rssi_dbm,
                "hops": s.hops,
                "tilt_deg": s.tilt_deg,
                "disp_mm": s.disp_mm,
                "ppv_mm_s": s.ppv_mm_s,
                "flags": s.flags,
                "received": s.received,
            }
            for s in self.nodes.values()
        ]
