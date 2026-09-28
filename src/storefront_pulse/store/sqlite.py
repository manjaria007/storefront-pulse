"""SQLite event/visit store. Same interface will back a DynamoDB adapter on AWS.

Tables
- events:   every Ring webhook we accepted (deduplicated by Ring's data.id)
- visits:   one row per traffic event after counting (people entering, confidence, method)
- context:  hourly weather/context per date (from Open-Meteo or the simulator)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  device_id TEXT,
  ts_ms INTEGER NOT NULL,
  sub_type TEXT,
  raw TEXT NOT NULL,
  processed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts_ms);
CREATE TABLE IF NOT EXISTS visits (
  event_id TEXT PRIMARY KEY REFERENCES events(event_id),
  ts_ms INTEGER NOT NULL,
  device_id TEXT,
  people INTEGER NOT NULL,
  confidence REAL NOT NULL,
  method TEXT NOT NULL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS ix_visits_ts ON visits(ts_ms);
CREATE TABLE IF NOT EXISTS context (
  local_date TEXT NOT NULL,
  hour INTEGER NOT NULL,
  precip_mm REAL,
  temp_c REAL,
  condition TEXT,
  source TEXT,
  PRIMARY KEY (local_date, hour)
);
"""


class Store:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")

    # ---------------------------------------------------------------- events
    def add_event(self, event_id: str, type_: str, device_id: str | None, ts_ms: int,
                  sub_type: str | None, raw: dict) -> bool:
        """Insert an event; returns False when it is a duplicate delivery."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO events(event_id,type,device_id,ts_ms,sub_type,raw) VALUES (?,?,?,?,?,?)",
                (event_id, type_, device_id, ts_ms, sub_type, json.dumps(raw)),
            )
            return cur.rowcount == 1

    def unprocessed_traffic_events(self, limit: int = 500) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM events WHERE processed=0 AND type IN ('motion_detected','button_press') "
                "ORDER BY ts_ms LIMIT ?", (limit,)).fetchall()

    def mark_processed(self, event_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE events SET processed=1 WHERE event_id=?", (event_id,))

    def events_between(self, start_ms: int, end_ms: int, types: Iterable[str] | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM events WHERE ts_ms>=? AND ts_ms<?"
        args: list = [start_ms, end_ms]
        if types:
            types = list(types)
            q += f" AND type IN ({','.join('?' * len(types))})"
            args += types
        with self._lock:
            return self._conn.execute(q + " ORDER BY ts_ms", args).fetchall()

    # ---------------------------------------------------------------- visits
    def add_visit(self, event_id: str, ts_ms: int, device_id: str | None, people: int,
                  confidence: float, method: str, note: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO visits VALUES (?,?,?,?,?,?,?)",
                (event_id, ts_ms, device_id, people, confidence, method, note),
            )

    def visits_between(self, start_ms: int, end_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM visits WHERE ts_ms>=? AND ts_ms<? ORDER BY ts_ms", (start_ms, end_ms)
            ).fetchall()

    def first_last_ts(self) -> tuple[int | None, int | None]:
        with self._lock:
            r = self._conn.execute("SELECT MIN(ts_ms), MAX(ts_ms) FROM visits").fetchone()
        return r[0], r[1]

    # --------------------------------------------------------------- context
    def put_context(self, rows: Iterable[tuple[str, int, float | None, float | None, str | None, str]]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("INSERT OR REPLACE INTO context VALUES (?,?,?,?,?,?)", list(rows))

    def context_for(self, local_date: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM context WHERE local_date=? ORDER BY hour", (local_date,)).fetchall()

    def close(self) -> None:
        self._conn.close()
