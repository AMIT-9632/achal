# SPDX-License-Identifier: MIT
"""Rate limiting and escalation. Pure logic with an injectable clock, so it is unit-tested."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from achal_common import Alert, Level
from recipients import RecipientsConfig


class RateLimiter:
    """Per recipient and channel: at most `per_hour` messages in a sliding hour, and
    no repeat of the same level inside the cooldown. The hourly cap never blocks a
    CRITICAL alert or a change of level (escalation or all-clear): those are the
    messages people must not miss."""

    def __init__(self, per_hour: int, same_level_cooldown_s: float) -> None:
        self.per_hour = per_hour
        self.cooldown = same_level_cooldown_s
        self.sent: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self.last_level: dict[tuple[str, str], tuple[Level, float]] = {}

    def check(self, recipient: str, channel: str, level: Level, now: float) -> str | None:
        """None if allowed, else the reason it is suppressed."""
        key = (recipient, channel)
        q = self.sent[key]
        while q and now - q[0] > 3600:
            q.popleft()
        prev = self.last_level.get(key)
        if prev and prev[0] == level and now - prev[1] < self.cooldown:
            return "suppressed_duplicate"
        must_send = level == Level.CRITICAL or (prev is not None and level != prev[0])
        if len(q) >= self.per_hour and not must_send:
            return "suppressed_rate_limit"
        return None

    def record(self, recipient: str, channel: str, level: Level, now: float) -> None:
        key = (recipient, channel)
        self.sent[key].append(now)
        self.last_level[key] = (level, now)


@dataclass
class Incident:
    """The open alert for a site and how far it has escalated."""

    alert: Alert
    opened: float
    tier: int = 0
    acked: bool = False
    notified: set[str] = field(default_factory=set)


class Escalator:
    """Tier 0 is told at once. If nobody acknowledges within the level's timeout,
    the next tier is told, and so on up to the highest tier configured."""

    def __init__(self, cfg: RecipientsConfig) -> None:
        self.cfg = cfg
        self.incident: Incident | None = None

    def open(self, alert: Alert, now: float) -> Incident | None:
        """Start tracking a new alert. Returns the incident it replaces, if any."""
        old = self.incident
        self.incident = Incident(alert=alert, opened=now) if alert.level != Level.NORMAL else None
        return old

    def ack(self, alert_id: str) -> bool:
        if self.incident and self.incident.alert.id == alert_id:
            self.incident.acked = True
            return True
        return False

    def due(self, now: float) -> int | None:
        """The tier to notify now, or None. Advances the incident when it returns a tier."""
        inc = self.incident
        if inc is None or inc.acked or inc.tier >= self.cfg.max_tier():
            return None
        timeout = self.cfg.escalation_min.get(inc.alert.level)
        if timeout is None:
            return None
        if now - inc.opened >= timeout * 60 * (inc.tier + 1):
            inc.tier += 1
            return inc.tier
        return None
