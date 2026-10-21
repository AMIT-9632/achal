# SPDX-License-Identifier: MIT
"""Render an Alert as a short message in one language, and size it for SMS."""

from __future__ import annotations

import json
import os
from pathlib import Path

from achal_common import Alert

TEMPLATES: dict[str, dict] = json.loads(
    (Path(__file__).parent / "templates" / "messages.json").read_text(encoding="utf-8"))

# Languages whose strings are an unreviewed draft fall back along this chain.
# Santali readers in Jharkhand usually read Hindi, so a reviewed Hindi warning
# is safer than an unreviewed Santali one.
FALLBACK = {"sat": "hi", "bn": "en", "hi": "en"}

GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
           "¿abcdefghijklmnopqrstuvwxyzäöñüà")


def is_usable(lang: str) -> bool:
    t = TEMPLATES.get(lang)
    if not t or "body" not in t:
        return False
    return t.get("_status") != "draft-unreviewed" or os.environ.get("ALERTS_ALLOW_DRAFT_LANGS") == "1"


def resolve_lang(lang: str) -> str:
    """The language a recipient actually gets, following FALLBACK past drafts."""
    seen = set()
    while not is_usable(lang) and lang not in seen:
        seen.add(lang)
        lang = FALLBACK.get(lang, "en")
    return lang if is_usable(lang) else "en"


def _ttf_phrase(t: dict, hours: float | None) -> str:
    if hours is None or hours <= 0:
        return ""
    if hours < 1:
        return t["ttf"].format(n=max(1, round(hours * 60)), unit=t["units"]["min"])
    return t["ttf"].format(n=f"{hours:.0f}" if hours >= 10 else f"{hours:.1f}", unit=t["units"]["h"])


def render(alert: Alert, lang: str, site_label: str | None = None, public: bool = False) -> str:
    """`public` drops node ids, which mean nothing to villagers, from the message."""
    t = TEMPLATES[resolve_lang(lang)]
    level = t["levels"][alert.level.value]
    shown = [t["reasons"].get(r, r) for r in alert.reasons if r != "level_change"]
    reasons = ", ".join(shown) or t["reasons"]["level_change"]
    site = site_label or alert.site
    if alert.level.value == "normal":
        return t["body_clear"].format(level=level, site=site)
    text = t["body_public" if public else "body"].format(
        level=level, site=site, nodes=", ".join(alert.nodes), reasons=reasons)
    text += _ttf_phrase(t, alert.ttf_hours)
    if alert.siren:
        text += " " + t["siren"]
    if alert.level.value == "critical":
        text += " " + t["action_critical"]
    return text


def sms_segments(text: str) -> tuple[str, int]:
    """(encoding, segment count). Indic scripts need UCS-2: 70 chars, or 67 per part when split."""
    if all(c in GSM7 for c in text):
        return "gsm7", 1 if len(text) <= 160 else -(-len(text) // 153)
    units = len(text.encode("utf-16-le")) // 2
    return "ucs2", 1 if units <= 70 else -(-units // 67)
