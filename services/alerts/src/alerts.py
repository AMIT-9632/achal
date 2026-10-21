# SPDX-License-Identifier: MIT
"""Alert notifier: renders each Alert in every site language and sends it.

SCAFFOLD STUB (W0). Logs the rendered message. Email (SMTP) and SMS (Twilio)
are sent only when their environment variables are set.

W4 adds: per-recipient language, rate limiting, escalation, push (FCM),
delivery receipts and IVR/voice for feature phones.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from achal_common import Alert, load_site, topics
from achal_common.mqtt import connect

log = logging.getLogger("alerts")
TEMPLATES = json.loads((Path(__file__).parent / "templates" / "messages.json").read_text(encoding="utf-8"))


def render(alert: Alert, lang: str) -> str:
    t = TEMPLATES.get(lang, TEMPLATES["en"])
    level = t["levels"][alert.level.value]
    reasons = ", ".join(t["reasons"].get(r, r) for r in alert.reasons)
    body = t["body_clear"] if alert.level.value == "normal" else t["body"]
    return body.format(level=level, site=alert.site, nodes=", ".join(alert.nodes), reasons=reasons)


def send_email(subject: str, body: str) -> None:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("ALERT_EMAIL_TO")
    if not (host and to):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_USER", "achal@localhost"), to
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    site = load_site()

    def on_message(_topic: str, payload: bytes) -> None:
        alert = Alert.model_validate_json(payload)
        texts = {lang: render(alert, lang) for lang in site.languages}
        for lang, text in texts.items():
            log.info("[%s] %s", lang, text)
        send_email(f"ACHAL {alert.level.value.upper()} - {site.name}", "\n\n".join(texts.values()))

    connect("alerts", on_message, [topics.alert(site.site_id)]).loop_forever()


if __name__ == "__main__":
    main()
