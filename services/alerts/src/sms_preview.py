# SPDX-License-Identifier: MIT
"""Render the mock SMS outbox as phone-screen mock-ups (one HTML file, no dependencies).

    python sms_preview.py [sms-outbox.jsonl] [-o sms-preview.html]

For the demo video and slides when no real SMS account is used. Every screen is
labelled as a simulated message.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ACHAL SMS preview</title>
<style>
body {{ margin: 0; padding: 24px 16px; background: #e9e9e4; color: #1d1d1b;
  font-family: system-ui, "Noto Sans", "Noto Sans Devanagari", "Noto Sans Bengali", "Noto Sans Ol Chiki", sans-serif; }}
h1 {{ font-size: 18px; margin: 0 0 4px; }} p.note {{ margin: 0 0 20px; color: #66665f; font-size: 13px; }}
.wall {{ display: flex; flex-wrap: wrap; gap: 24px; }}
.phone {{ width: 300px; border-radius: 34px; background: #111; padding: 14px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }}
.screen {{ background: #f5f5f0; border-radius: 22px; min-height: 420px; padding: 14px 12px;
  display: flex; flex-direction: column; }}
.bar {{ display: flex; justify-content: space-between; font-size: 12px; color: #444; }}
.from {{ text-align: center; font-weight: 600; margin: 14px 0 2px; }}
.sub {{ text-align: center; font-size: 11px; color: #777; margin-bottom: 14px; }}
.bubble {{ background: #fff; border-radius: 18px 18px 18px 4px; padding: 10px 12px; font-size: 15px; line-height: 1.45;
  box-shadow: 0 1px 2px rgba(0,0,0,.12); white-space: pre-wrap; }}
.meta {{ font-size: 11px; color: #777; margin-top: 6px; }}
.sim {{ margin-top: auto; text-align: center; font-size: 11px; color: #a0522d; }}
</style></head><body>
<h1>ACHAL alert SMS, as received</h1>
<p class="note">Simulated delivery through the mock SMS gateway. Synthetic alert data. Santali recipients receive Hindi
until the Santali text is reviewed by a native speaker.</p>
<div class="wall">{phones}</div>
</body></html>
"""

PHONE = """<div class="phone"><div class="screen">
<div class="bar"><span>{time}</span><span>4G ▮▮▮▯</span></div>
<div class="from">ACHAL-ALERT</div><div class="sub">{to}</div>
<div class="bubble" lang="{lang}">{text}</div>
<div class="meta">{encoding} · {parts} SMS part{plural}</div>
<div class="sim">simulated · not a real message</div>
</div></div>"""


def lang_of(text: str) -> str:
    if any("ऀ" <= c <= "ॿ" for c in text):
        return "hi"
    if any("ঀ" <= c <= "৿" for c in text):
        return "bn"
    return "en"


def mask(number: str) -> str:
    return number[:-4].rstrip() + " ••••" if len(number) > 6 else number


def render(rows: list[dict]) -> str:
    phones = []
    for r in rows:
        t = datetime.fromisoformat(r["ts"]).strftime("%H:%M")
        phones.append(PHONE.format(time=t, to=html.escape(mask(r["to"])), lang=lang_of(r["text"]),
                                   text=html.escape(r["text"]), encoding=r["encoding"].upper(), parts=r["parts"],
                                   plural="" if r["parts"] == 1 else "s"))
    return PAGE.format(phones="\n".join(phones))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outbox", nargs="?", default="sms-outbox.jsonl")
    ap.add_argument("-o", "--out", default="sms-preview.html")
    ap.add_argument("--last", type=int, default=8, help="how many of the newest messages to show")
    args = ap.parse_args()
    lines = Path(args.outbox).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(x) for x in lines if x.strip()][-args.last:]
    Path(args.out).write_text(render(rows), encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} messages)")


if __name__ == "__main__":
    main()
