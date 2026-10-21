# SPDX-License-Identifier: MIT
"""Append-only SQLite log of every message attempt, for audit and KPIs (delivery rate, latency)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime


class DeliveryLog:
    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS delivery (
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, alert_id TEXT NOT NULL, level TEXT NOT NULL,
            recipient TEXT NOT NULL, channel TEXT NOT NULL, lang TEXT NOT NULL, tier INTEGER NOT NULL,
            status TEXT NOT NULL, provider_ref TEXT, error TEXT, segments INTEGER, text TEXT)""")
        self.lock = threading.Lock()

    def add(self, **row) -> dict:
        row = {"ts": datetime.now(UTC).isoformat(), **row}
        cols = ", ".join(row)
        with self.lock:
            self.db.execute(f"INSERT INTO delivery ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
            self.db.commit()
        return row

    def rows(self, alert_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM delivery", ()
        if alert_id:
            q, args = q + " WHERE alert_id = ?", (alert_id,)
        with self.lock:
            cur = self.db.execute(q + " ORDER BY id", args)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def summary(self) -> dict[str, int]:
        with self.lock:
            return dict(self.db.execute("SELECT status, COUNT(*) FROM delivery GROUP BY status").fetchall())
