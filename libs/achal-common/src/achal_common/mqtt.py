# SPDX-License-Identifier: MIT
"""Thin paho-mqtt wrapper with retry-on-connect, shared by all services."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)


def connect(client_id: str, on_message: Callable[[str, bytes], None] | None = None,
            subscriptions: list[str] | None = None) -> mqtt.Client:
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)

    def _on_connect(c, _userdata, _flags, reason_code, _props):
        log.info("connected to mqtt %s:%s (%s)", host, port, reason_code)
        for topic in subscriptions or []:
            c.subscribe(topic, qos=1)

    client.on_connect = _on_connect
    if on_message:
        client.on_message = lambda _c, _u, msg: on_message(msg.topic, msg.payload)

    while True:
        try:
            client.connect(host, port, keepalive=30)
            break
        except OSError as exc:
            log.warning("mqtt not reachable (%s), retrying in 2 s", exc)
            time.sleep(2)
    return client
