# SPDX-License-Identifier: MIT
"""MQTT topic names. See docs/mqtt-topics.md."""

ROOT = "achal"


def telemetry(site: str, node: str = "+") -> str:
    return f"{ROOT}/{site}/telemetry/{node}"


def risk(site: str, node: str = "+") -> str:
    return f"{ROOT}/{site}/risk/{node}"


def risk_grid(site: str) -> str:
    return f"{ROOT}/{site}/risk/grid"


def alert(site: str) -> str:
    return f"{ROOT}/{site}/alert"


def gateway_status(site: str) -> str:
    return f"{ROOT}/{site}/gateway/status"


def siren_cmd(site: str, siren_id: str = "+") -> str:
    return f"{ROOT}/{site}/siren/{siren_id}/cmd"
