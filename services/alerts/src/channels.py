# SPDX-License-Identifier: MIT
"""Delivery channels. Each `send` returns a provider reference or raises.

SMS providers use plain HTTPS (stdlib urllib) so no SDK is needed:
- twilio:   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM
- fast2sms: FAST2SMS_API_KEY (Indian gateway, Quick SMS route, Indian numbers only)
- console:  logs the message; the default, and what demos use without credentials.
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
from email.message import EmailMessage
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
    return ConsoleChannel("sms")


def email_from_env() -> Channel:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return ConsoleChannel("email")
    user = os.environ.get("SMTP_USER") or None
    return SmtpEmail(host, int(os.environ.get("SMTP_PORT", "587")), user, os.environ.get("SMTP_PASSWORD"),
                     os.environ.get("SMTP_FROM") or user or "achal@localhost")
