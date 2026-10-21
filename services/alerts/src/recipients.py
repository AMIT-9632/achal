# SPDX-License-Identifier: MIT
"""Who gets told, in which language, over which channel, and when.

The committed file config/recipients/<site>.example.yaml holds fictional
people and dummy numbers. Real contacts go in a file named by RECIPIENTS_FILE
(config/recipients/*.local.yaml is gitignored) and are never committed.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from achal_common import Level
from achal_common.config import config_dir
from pydantic import BaseModel, Field


class Recipient(BaseModel):
    id: str
    name: str
    role: str
    lang: str = "hi"
    sms: str | None = Field(default=None, description="E.164, e.g. +9198xxxxxxxx")
    email: str | None = None
    min_level: Level = Level.WARNING
    tier: int = Field(default=0, ge=0, description="0 = told at once, 1+ = told on escalation")
    public: bool = Field(default=False, description="Community member: plain message without node ids")

    def channels(self) -> list[str]:
        return [c for c in ("sms", "email") if getattr(self, c)]

    def wants(self, level: Level) -> bool:
        # Everyone who was told about a problem also hears the all-clear.
        return level == Level.NORMAL or level.rank >= self.min_level.rank


class RateLimit(BaseModel):
    per_hour: int = 6
    same_level_cooldown_min: float = 30


class RecipientsConfig(BaseModel):
    site: str
    site_label: dict[str, str] = {}
    escalation_min: dict[Level, float | None] = {Level.WATCH: None, Level.WARNING: 15, Level.CRITICAL: 5}
    rate_limit: RateLimit = RateLimit()
    recipients: list[Recipient]

    def label(self, lang: str) -> str | None:
        return self.site_label.get(lang) or self.site_label.get("en")

    def max_tier(self) -> int:
        return max((r.tier for r in self.recipients), default=0)


def load_recipients(site_id: str) -> RecipientsConfig:
    path = os.environ.get("RECIPIENTS_FILE")
    p = Path(path) if path else config_dir() / "recipients" / f"{site_id}.example.yaml"
    with p.open(encoding="utf-8") as f:
        return RecipientsConfig.model_validate(yaml.safe_load(f))
