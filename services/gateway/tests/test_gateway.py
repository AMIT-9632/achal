# SPDX-License-Identifier: MIT
import json
from datetime import UTC, datetime, timedelta

from achal_common import Telemetry, load_site, topics
from gateway import Buffer, Gateway
from rules import LocalRules, Thresholds

SITE = load_site("jharia-demo")
T0 = datetime(2026, 10, 21, tzinfo=UTC)


class FakeInfo:
    def __init__(self, mid: int, rc: int = 0) -> None:
        self.mid, self.rc = mid, rc


class FakeBroker:
    """Stands in for paho: records publishes, acknowledges only when told to."""

    def __init__(self) -> None:
        self.connected = True
        self.sent: list[tuple[int, str, str]] = []
        self.mid = 0

    def is_connected(self) -> bool:
        return self.connected

    def publish(self, topic, payload, qos=0, retain=False):
        if not self.connected:
            return FakeInfo(0, rc=4)
        self.mid += 1
        self.sent.append((self.mid, topic, payload))
        return FakeInfo(self.mid)


def frame(node: str, seq: int, **over) -> bytes:
    values = {"tilt_x_deg": 0.0, "tilt_y_deg": 0.0, "vib_rms_g": 0.0005, "vib_peak_g": 0.002, "crack_mm": 0.0,
              "ext_mm": 0.0, "batt_v": 3.9, "hops": 1, "path": [node], "rssi_dbm": -90.0, **over}
    t = Telemetry(site=SITE.site_id, node=node, seq=seq, ts=T0 + timedelta(minutes=seq), **values)
    return t.model_dump_json(exclude_none=True).encode()


def make(tmp_path, client=None, rules=None):
    client = client or FakeBroker()
    return Gateway(SITE, Buffer(str(tmp_path / "b.sqlite")), client, rules), client


def ack_all(gw: Gateway, broker: FakeBroker) -> None:
    for mid, _, _ in list(broker.sent):
        gw.on_publish(mid)


def test_duplicates_from_two_paths_are_dropped(tmp_path):
    gw, _ = make(tmp_path)
    assert gw.handle_frame(frame("n03", 5)) is not None
    assert gw.handle_frame(frame("n03", 5, path=["n03", "n08"], hops=2)) is None
    assert gw.handle_frame(frame("n03", 6)) is not None
    s = gw.status()
    assert (s["frames_rx"], s["frames_duplicate"], s["readings_unique"], s["buffered"]) == (3, 1, 2, 2)


def test_dedupe_survives_gateway_restart(tmp_path):
    gw, _ = make(tmp_path)
    gw.handle_frame(frame("n01", 1))
    gw2, _ = make(tmp_path)
    assert gw2.handle_frame(frame("n01", 1)) is None


def test_bad_and_foreign_frames_are_counted(tmp_path):
    gw, _ = make(tmp_path)
    assert gw.handle_frame(b"not json") is None
    other = json.loads(frame("n01", 1))
    other["site"] = "elsewhere"
    assert gw.handle_frame(json.dumps(other).encode()) is None
    assert gw.status()["frames_bad"] == 2


def test_rows_leave_the_outbox_only_after_puback(tmp_path):
    gw, broker = make(tmp_path)
    for k in range(5):
        gw.handle_frame(frame("n02", k))
    assert gw.flush_once() == 5
    assert gw.buf.size() == 5  # sent, not yet acknowledged
    assert gw.flush_once() == 0  # nothing is sent twice while in flight
    ack_all(gw, broker)
    assert gw.buf.size() == 0 and gw.status()["published"] == 5


def test_uplink_outage_loses_nothing_and_replays_in_order(tmp_path):
    gw, broker = make(tmp_path)
    gw.handle_frame(frame("n00", 0))
    gw.flush_once()
    broker.connected = False
    gw.on_disconnect()  # the in-flight message was never acknowledged
    for k in range(1, 200):
        gw.handle_frame(frame("n00", k))
    assert gw.flush_once() == 0 and gw.buf.size() == 200
    broker.connected = True
    while gw.buf.size():
        gw.flush_once()
        ack_all(gw, broker)
        broker.sent.clear()
    assert gw.status()["published"] == 200
    assert gw.status()["replayed"] == 199


def test_replay_order_is_arrival_order(tmp_path):
    gw, broker = make(tmp_path)
    broker.connected = False
    for k in range(50):
        gw.handle_frame(frame(f"n{k % 25:02d}", k))
    broker.connected = True
    gw.flush_once()
    seqs = [json.loads(p)["seq"] for _, t, p in broker.sent if "/telemetry/" in t]
    assert seqs == list(range(50))


def _collapse(gw: Gateway, nodes: list[str], start: int = 0) -> list:
    """Baseline readings, then a sag at the given nodes."""
    for k in range(start, start + 10):
        for n in SITE.node_ids():
            gw.handle_frame(frame(n, k))
    for k in range(start + 10, start + 13):
        for n in SITE.node_ids():
            if n in nodes:
                gw.handle_frame(frame(n, k, tilt_x_deg=0.5, crack_mm=8.0, vib_peak_g=0.3))
            else:
                gw.handle_frame(frame(n, k))
    return [p for _, t, p in getattr(gw.client, "sent", [])]


def test_local_siren_sounds_with_uplink_down(tmp_path):
    gw, broker = make(tmp_path)
    broker.connected = False
    _collapse(gw, ["n12", "n13", "n17"])
    on = {k: v for k, v in gw.sirens.items() if v["on"]}
    assert on, "a siren must sound locally with no uplink"
    assert all(v["level"] == "critical" and v["source"] == "gateway" for v in on.values())
    # The command is waiting at the front of the outbox for when the uplink returns.
    broker.connected = True
    gw.flush_once(batch=len(on))
    assert all(t.endswith("/cmd") for _, t, _ in broker.sent)
    assert {t.split("/")[3] for _, t, _ in broker.sent} == set(on)


def test_single_node_spike_does_not_sound_siren(tmp_path):
    gw, _ = make(tmp_path)
    _collapse(gw, ["n12"])
    assert not any(v["on"] for v in gw.sirens.values())


def test_one_bad_reading_is_not_an_alarm():
    rules = LocalRules(SITE, Thresholds())
    out = []
    for k in range(12):
        for n in ("n12", "n13"):
            spike = k == 11
            out += rules.observe(Telemetry.model_validate_json(frame(n, k, crack_mm=9.0 if spike else 0.0)))
    assert out == []


def test_siren_turns_off_after_hold(tmp_path):
    rules = LocalRules(SITE, Thresholds(hold_s=600))
    gw, _ = make(tmp_path, rules=rules)
    _collapse(gw, ["n12", "n13"])
    assert any(v["on"] for v in gw.sirens.values())
    # 40 quiet minutes later (readings back to baseline, alarms aged out of the window).
    for k in range(13, 60):
        for n in SITE.node_ids():
            gw.handle_frame(frame(n, k))
    assert not any(v["on"] for v in gw.sirens.values())


def test_cloud_siren_command_is_relayed_but_not_echoed(tmp_path):
    gw, _ = make(tmp_path)
    gw.on_cloud_siren_cmd(topics.siren_cmd(SITE.site_id, "siren-02"),
                          json.dumps({"on": True, "level": "warning", "ts": T0.isoformat()}).encode())
    assert gw.sirens["siren-02"]["on"] and gw.sirens["siren-02"]["source"] == "cloud"
    gw.on_cloud_siren_cmd(topics.siren_cmd(SITE.site_id, "siren-01"),
                          json.dumps({"on": True, "source": "gateway"}).encode())
    assert not gw.sirens["siren-01"]["on"]


def test_metrics_text_is_prometheus(tmp_path):
    gw, _ = make(tmp_path)
    gw.handle_frame(frame("n04", 1))
    text = gw.metrics_text()
    assert 'achal_gateway_readings_unique_total{gateway="gw-01",site="jharia-demo"} 1' in text
    assert 'achal_node_rssi_dbm{gateway="gw-01",site="jharia-demo",node="n04"} -90.0' in text
    assert "achal_gateway_uplink_online" in text
