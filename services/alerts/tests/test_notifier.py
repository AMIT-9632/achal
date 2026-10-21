# SPDX-License-Identifier: MIT
from datetime import UTC, datetime

import pytest
from achal_common import Alert, Level
from alerts import Notifier
from delivery_log import DeliveryLog
from messages import render, resolve_lang, sms_segments
from policy import RateLimiter
from recipients import load_recipients


class FakeChannel:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.sent = name, fail, []

    def send(self, to, subject, text):
        if self.fail:
            raise RuntimeError("provider down")
        self.sent.append((to, text))
        return f"ref{len(self.sent)}"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def alert(level, id_="a1", **kw):
    return Alert(id=id_, site="jharia-demo", ts=datetime.now(UTC), level=level, nodes=["n12"],
                 reasons=kw.pop("reasons", ["crack_opening"]), **kw)


@pytest.fixture
def setup(tmp_path):
    cfg = load_recipients("jharia-demo")
    sms, email = FakeChannel("sms"), FakeChannel("email")
    clock = Clock()
    published = []
    n = Notifier(cfg, {"sms": sms, "email": email}, DeliveryLog(str(tmp_path / "d.sqlite")), published.append, clock)
    return n, sms, email, clock, published


def who(channel):
    return {to for to, _ in channel.sent}


def test_watch_only_reaches_people_who_asked_for_it(setup):
    n, sms, email, _, _ = setup
    n.on_alert(alert(Level.WATCH))
    assert who(sms) == {"+910000000001"}          # safety officer only
    assert who(email) == {"safety-officer@example.org"}


def test_each_recipient_gets_their_own_language(setup):
    n, sms, _, _, _ = setup
    n.on_alert(alert(Level.CRITICAL, siren=True))
    texts = dict(sms.sent)
    assert "DANGER" in texts["+910000000001"]
    assert "বিপদ" in texts["+910000000002"]             # Bengali overman
    assert "खतरा" in texts["+910000000003"]              # Hindi sarpanch
    assert "खतरा" in texts["+910000000004"]              # Santali draft falls back to Hindi
    assert "n12" not in texts["+910000000003"]           # public message has no node ids
    assert "n12" in texts["+910000000002"]


def test_escalates_until_acknowledged(setup):
    n, sms, email, clock, _ = setup
    n.on_alert(alert(Level.CRITICAL))
    assert "+910000000005" not in who(sms)
    clock.t += 4 * 60
    n.tick()
    assert "+910000000005" not in who(sms)
    clock.t += 2 * 60                                    # 6 min > 5 min critical timeout
    n.tick()
    assert "+910000000005" in who(sms)                   # tier 1 colliery agent
    assert "ddmo@example.org" not in who(email)
    clock.t += 5 * 60
    n.tick()
    assert "ddmo@example.org" in who(email)              # tier 2 district officer


def test_ack_stops_escalation(setup):
    n, sms, _, clock, _ = setup
    n.on_alert(alert(Level.WARNING))
    n.on_ack("a1", "overman")
    clock.t += 60 * 60
    n.tick()
    assert "+910000000005" not in who(sms)


def test_all_clear_goes_to_everyone_told(setup):
    n, sms, _, clock, _ = setup
    n.on_alert(alert(Level.WARNING))
    clock.t += 16 * 60
    n.tick()                                              # tier 1 told
    before = len(sms.sent)
    n.on_alert(alert(Level.NORMAL, id_="a2", reasons=["level_change"]))
    cleared = {to for to, _ in sms.sent[before:]}
    assert cleared == {"+910000000001", "+910000000002", "+910000000003", "+910000000004", "+910000000005"}


def test_rate_limit_suppresses_repeats_but_never_critical(setup):
    n, sms, _, clock, published = setup
    n.on_alert(alert(Level.WARNING, id_="w1"))
    n.on_alert(alert(Level.WARNING, id_="w2"))           # same level again within cooldown
    assert any(p["status"] == "suppressed_duplicate" for p in published)
    before = len(sms.sent)
    n.on_alert(alert(Level.CRITICAL, id_="c1"))
    assert len(sms.sent) - before == 4                    # all tier-0 SMS recipients


def test_rate_limiter_hourly_cap():
    rl = RateLimiter(per_hour=2, same_level_cooldown_s=0)
    t = 0.0
    for lv in (Level.WATCH, Level.WARNING):
        assert rl.check("r", "sms", lv, t) is None
        rl.record("r", "sms", lv, t)
        t += 1
    assert rl.check("r", "sms", Level.WARNING, t) == "suppressed_rate_limit"
    assert rl.check("r", "sms", Level.CRITICAL, t) is None
    assert rl.check("r", "sms", Level.WARNING, t + 3601) is None


def test_failed_provider_is_logged_and_others_still_sent(tmp_path):
    cfg = load_recipients("jharia-demo")
    sms, email = FakeChannel("sms", fail=True), FakeChannel("email")
    log = DeliveryLog(str(tmp_path / "d.sqlite"))
    Notifier(cfg, {"sms": sms, "email": email}, log).on_alert(alert(Level.WATCH))
    assert who(email) == {"safety-officer@example.org"}
    assert log.summary() == {"failed": 1, "sent": 1}


def test_published_delivery_records_carry_no_contact_details(setup):
    n, _, _, _, published = setup
    n.on_alert(alert(Level.CRITICAL))
    assert published
    for p in published:
        assert "+91" not in str(p) and "@" not in str(p)


def test_sms_segments():
    assert sms_segments("ACHAL Warning")[0] == "gsm7"
    enc, parts = sms_segments(render(alert(Level.CRITICAL, ttf_hours=3.0, siren=True), "hi"))
    assert enc == "ucs2" and parts >= 2


def test_language_resolution():
    assert resolve_lang("sat") == "hi"
    assert resolve_lang("xx") == "en"
    assert resolve_lang("bn") == "bn"


def test_ttf_phrase():
    assert "40 min" in render(alert(Level.WARNING, ttf_hours=0.66), "en")
    assert "5.5 hours" in render(alert(Level.WARNING, ttf_hours=5.5), "en")
