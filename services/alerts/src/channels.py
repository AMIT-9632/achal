# SPDX-License-Identifier: MIT
"""Delivery channels. Each `send` returns a provider reference or raises.

SMS providers use plain HTTPS (stdlib urllib) so no SDK is needed:
- twilio:   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM
- fast2sms: FAST2SMS_API_KEY (Indian gateway, Quick SMS route, Indian numbers only)
- mock:     the default without credentials. Behaves like a gateway (message id,
            GSM-7/UCS-2 segment count) and appends each SMS to an outbox file
            (MOCK_SMS_OUTBOX) that sms_preview.py turns into phone mock-ups.
- console:  only logs the message.
Indian commercial SMS needs a DLT-registered sender and templates (TRAI); the
Quick/international routes above are for testing and the demo video only.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import smtplib
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

log = logging.getLogger("alerts.channels")
TIMEOUT_S = 15


class Channel(Protocol):
    name: str

    def send(self, to: str, subject: str, text: str) -> str: ...


def _post(url: str, data: bytes, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 - fixed https URLs
        return json.loads(r.read().decode() or "{}")


class ConsoleChannel:
    def __init__(self, name: str) -> None:
        self.name = name

    def send(self, to: str, subject: str, text: str) -> str:
        log.info("[%s -> %s] %s", self.name, to, text)
        return "console"


class MockSms:
    """Stand-in SMS gateway for demos and tests: nothing leaves the machine."""

    name = "sms"
    mock = True

    def __init__(self, outbox: str | None) -> None:
        self.outbox = Path(outbox) if outbox else None

    def send(self, to: str, subject: str, text: str) -> str:
        from messages import sms_segments

        encoding, parts = sms_segments(text)
        ref = f"mock-{uuid.uuid4().hex[:10]}"
        log.info("[sms mock -> %s] (%s, %d part%s) %s", to, encoding, parts, "" if parts == 1 else "s", text)
        if self.outbox:
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            row = {"ref": ref, "ts": datetime.now(UTC).isoformat(), "to": to, "encoding": encoding,
                   "parts": parts, "text": text}
            with self.outbox.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return ref


class TwilioSms:
    name = "sms"

    def __init__(self, sid: str, token: str, sender: str) -> None:
        self.sid, self.token, self.sender = sid, token, sender

    def send(self, to: str, subject: str, text: str) -> str:
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        body = urllib.parse.urlencode({"From": self.sender, "To": to, "Body": text}).encode()
        r = _post(f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json", body,
                  {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
        return str(r.get("sid", ""))


class Fast2Sms:
    name = "sms"

    def __init__(self, key: str) -> None:
        self.key = key

    def send(self, to: str, subject: str, text: str) -> str:
        number = to.lstrip("+")
        if len(number) == 12 and number.startswith("91"):
            number = number[2:]  # Fast2SMS takes 10-digit Indian numbers
        body = json.dumps({"route": "q", "message": text, "flash": 0, "numbers": number}).encode()
        r = _post("https://www.fast2sms.com/dev/bulkV2", body,
                  {"authorization": self.key, "Content-Type": "application/json"})
        if not r.get("return"):
            raise RuntimeError(f"fast2sms: {r.get('message')}")
        return str(r.get("request_id", ""))


class SmtpEmail:
    name = "email"

    def __init__(self, host: str, port: int, user: str | None, password: str | None, sender: str) -> None:
        self.host, self.port, self.user, self.password, self.sender = host, port, user, password, sender

    def send(self, to: str, subject: str, text: str) -> str:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, self.sender, to
        msg.set_content(text)
        with smtplib.SMTP(self.host, self.port, timeout=TIMEOUT_S) as s:
            s.starttls()
            if self.user:
                s.login(self.user, self.password or "")
            s.send_message(msg)
        return msg.get("Message-ID", "smtp")


def sms_from_env() -> Channel:
    provider = os.environ.get("SMS_PROVIDER", "").lower()
    if provider == "twilio" or (not provider and os.environ.get("TWILIO_ACCOUNT_SID")):
        return TwilioSms(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"], os.environ["TWILIO_FROM"])
    if provider == "fast2sms" or (not provider and os.environ.get("FAST2SMS_API_KEY")):
        return Fast2Sms(os.environ["FAST2SMS_API_KEY"])
    if provider == "console":
        return ConsoleChannel("sms")
    return MockSms(os.environ.get("MOCK_SMS_OUTBOX") or "/data/sms-outbox.jsonl")


def email_from_env() -> Channel:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return ConsoleChannel("email")
    user = os.environ.get("SMTP_USER") or None
    return SmtpEmail(host, int(os.environ.get("SMTP_PORT", "587")), user, os.environ.get("SMTP_PASSWORD"),
                     os.environ.get("SMTP_FROM") or user or "achal@localhost")
