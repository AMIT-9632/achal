# SPDX-License-Identifier: MIT
import json
from datetime import UTC, datetime

from achal_common import Alert, Level
from alerts import Notifier
from channels import MockSms, sms_from_env
from delivery_log import DeliveryLog
from recipients import load_recipients
from sms_preview import render


def test_mock_is_the_default_without_credentials(monkeypatch):
    for k in ("SMS_PROVIDER", "TWILIO_ACCOUNT_SID", "FAST2SMS_API_KEY", "MOCK_SMS_OUTBOX"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(sms_from_env(), MockSms)


def test_mock_gateway_writes_outbox_and_logs_sent_mock(tmp_path):
    outbox = tmp_path / "out" / "sms.jsonl"
    log = DeliveryLog(str(tmp_path / "d.sqlite"))
    published = []
    n = Notifier(load_recipients("jharia-demo"), {"sms": MockSms(str(outbox))}, log, published.append)
    n.on_alert(Alert(id="a1", site="jharia-demo", ts=datetime.now(UTC), level=Level.CRITICAL, nodes=["n12"],
                     reasons=["crack_opening"], siren=True))
    rows = [json.loads(x) for x in outbox.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4                                       # every tier-0 SMS recipient
    assert {r["encoding"] for r in rows} == {"gsm7", "ucs2"}    # English vs Indic scripts
    assert all(r["ref"].startswith("mock-") for r in rows)
    sms_status = {p["status"] for p in published if p["channel"] == "sms"}
    assert sms_status == {"sent_mock"}

    page = render(rows)
    assert page.count('class="phone"') == 4
    assert "simulated" in page and "+910000000001" not in page   # numbers are masked
