# SPDX-License-Identifier: MIT
"""Alert notifier: tells the right people, in their language, and escalates until someone acts.

Listens on achal/{site}/alert and achal/{site}/alert/ack. For each alert it
messages every tier-0 recipient whose min_level it reaches, in their language,
by SMS and/or email, under a per-recipient rate limit. If no operator
acknowledges in time, the next tier is told. Every attempt goes to a SQLite
delivery log and to achal/{site}/alert/delivery for the dashboard.

Send one test message (for the demo video). With no SMS credentials it goes to the
mock gateway and lands in sms-outbox.jsonl; `python sms_preview.py` renders it:
    python alerts.py --test-send --to +9198XXXXXXXX --lang all
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from achal_common import Alert, Level, load_site, topics
from channels import Channel, email_from_env, sms_from_env
from delivery_log import DeliveryLog
from messages import TEMPLATES, render, resolve_lang, sms_segments
from policy import Escalator, RateLimiter
from recipients import Recipient, RecipientsConfig, load_recipients

__all__ = ["TEMPLATES", "Notifier", "render"]

log = logging.getLogger("alerts")


class Notifier:
    def __init__(self, cfg: RecipientsConfig, channels: dict[str, Channel], delivery_log: DeliveryLog,
                 publish: Callable[[dict], None] = lambda _d: None, clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.channels = channels
        self.log = delivery_log
        self.publish = publish
        self.clock = clock
        self.limiter = RateLimiter(cfg.rate_limit.per_hour, cfg.rate_limit.same_level_cooldown_min * 60)
        self.escalator = Escalator(cfg)
        self.lock = threading.Lock()

    def on_alert(self, alert: Alert) -> None:
        with self.lock:
            old = self.escalator.open(alert, self.clock())
            reached = old.tier if old else 0
            if alert.level == Level.NORMAL:
                # All clear goes to everyone who heard about the problem.
                told = old.notified if old else set()
                targets = [r for r in self.cfg.recipients if r.id in told]
            else:
                targets = [r for r in self.cfg.recipients if r.tier <= reached and r.wants(alert.level)]
            inc = self.escalator.incident
            if inc and old:
                inc.tier = reached
            for r in targets:
                self._notify(r, alert, tier=r.tier)

    def on_ack(self, alert_id: str, by: str) -> None:
        with self.lock:
            if self.escalator.ack(alert_id):
                log.info("alert %s acknowledged by %s, escalation stopped", alert_id, by)

    def tick(self) -> None:
        with self.lock:
            tier = self.escalator.due(self.clock())
            inc = self.escalator.incident
            if tier is None or inc is None:
                return
            log.warning("alert %s not acknowledged, escalating to tier %d", inc.alert.id, tier)
            for r in self.cfg.recipients:
                if r.tier == tier and r.wants(inc.alert.level):
                    self._notify(r, inc.alert, tier=tier)

    def _notify(self, r: Recipient, alert: Alert, tier: int) -> None:
        lang = resolve_lang(r.lang)
        text = render(alert, lang, self.cfg.label(lang), public=r.public)
        subject = f"ACHAL {alert.level.value.upper()} - {self.cfg.label('en') or alert.site}"
        inc = self.escalator.incident
        for ch in r.channels():
            now = self.clock()
            base = {"alert_id": alert.id, "level": alert.level.value, "recipient": r.id, "channel": ch,
                    "lang": lang, "tier": tier}
            reason = self.limiter.check(r.id, ch, alert.level, now)
            if reason:
                self._record(base, status=reason)
                continue
            channel = self.channels.get(ch)
            if channel is None:
                self._record(base, status="no_channel")
                continue
            segments = sms_segments(text)[1] if ch == "sms" else None
            try:
                ref = channel.send(getattr(r, ch), subject, text)
            except Exception as exc:  # noqa: BLE001 - one failed provider call must not stop the others
                log.error("send %s to %s failed: %s", ch, r.id, exc)
                self._record(base, status="failed", error=str(exc)[:300], segments=segments, text=text)
                continue
            self.limiter.record(r.id, ch, alert.level, now)
            if inc:
                inc.notified.add(r.id)
            # "sent_mock" keeps demo deliveries honest in the log and on the dashboard.
            status = "sent_mock" if getattr(channel, "mock", False) else "sent"
            self._record(base, status=status, provider_ref=ref, segments=segments, text=text)

    def _record(self, base: dict, status: str, **extra) -> None:
        row = self.log.add(**base, status=status, **extra)
        # Published copy carries no phone numbers or message bodies.
        self.publish({k: row[k] for k in ("alert_id", "recipient", "channel", "lang", "tier", "status", "ts")})


def channels_from_env() -> dict[str, Channel]:
    return {"sms": sms_from_env(), "email": email_from_env()}


def test_send(to: str, lang: str, level: str) -> None:
    """Send one sample alert to one number or address, outside MQTT. lang "all" sends one per language."""
    os.environ["MOCK_SMS_OUTBOX"] = os.environ.get("MOCK_SMS_OUTBOX") or "sms-outbox.jsonl"  # local file by hand
    alert = Alert(id=f"test-{uuid.uuid4().hex[:8]}", site="jharia-demo", ts=datetime.now(UTC), level=Level(level),
                  nodes=["n12", "n13"], reasons=["crack_opening", "neighbour_consensus"], ttf_hours=5.5,
                  siren=Level(level).rank >= Level.WARNING.rank)
    cfg = RecipientsConfig(site="jharia-demo", site_label={"en": "Jharia test site", "hi": "झरिया टेस्ट साइट",
                                                           "bn": "ঝরিয়া টেস্ট সাইট"},
                           recipients=[Recipient(id=f"test-{lg}", name="Test", role="test", lang=lg,
                                                 min_level=Level.WATCH, **({"email": to} if "@" in to else {"sms": to}))
                                       for lg in (list(TEMPLATES) if lang == "all" else [lang])])
    n = Notifier(cfg, channels_from_env(), DeliveryLog(os.environ.get("DELIVERY_LOG", "deliveries.sqlite")),
                 publish=lambda d: print(json.dumps(d, ensure_ascii=False)))
    n.on_alert(alert)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test-send", action="store_true", help="send one sample alert and exit")
    ap.add_argument("--to", help="phone (E.164) or email for --test-send")
    ap.add_argument("--lang", default="hi", help="en, hi, bn, sat, or all")
    ap.add_argument("--level", default="critical", choices=[lv.value for lv in Level])
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.test_send:
        if not args.to:
            ap.error("--test-send needs --to")
        test_send(args.to, args.lang, args.level)
        return

    from achal_common.mqtt import connect

    site = load_site()
    cfg = load_recipients(site.site_id)
    dlog = DeliveryLog(os.environ.get("DELIVERY_LOG", "/data/deliveries.sqlite"))
    client = None

    def publish(d: dict) -> None:
        if client is not None:
            client.publish(topics.alert_delivery(site.site_id), json.dumps(d, ensure_ascii=False), qos=1)

    notifier = Notifier(cfg, channels_from_env(), dlog, publish)
    log.info("alerts ready: %d recipients, sms=%s, email=%s", len(cfg.recipients),
             type(notifier.channels["sms"]).__name__, type(notifier.channels["email"]).__name__)

    def on_message(topic: str, payload: bytes) -> None:
        try:
            if topic == topics.alert_ack(site.site_id):
                d = json.loads(payload)
                notifier.on_ack(d["id"], d.get("by", "?"))
            else:
                notifier.on_alert(Alert.model_validate_json(payload))
        except (ValueError, KeyError) as exc:
            log.warning("ignoring bad message on %s: %s", topic, exc)

    def escalate_forever() -> None:
        while True:
            notifier.tick()
            time.sleep(5)

    threading.Thread(target=escalate_forever, daemon=True).start()
    client = connect("alerts", on_message, [topics.alert(site.site_id), topics.alert_ack(site.site_id)])
    client.loop_forever()


if __name__ == "__main__":
    main()
