"""Persists each validation run so the dashboard's pipeline catalog can list
past models and their verdicts without re-running anything.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from common.state_db import connect as _connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS validation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    model_name TEXT NOT NULL,
    sql_path TEXT,
    verdict TEXT NOT NULL,
    flags_json TEXT,
    report_json TEXT NOT NULL
);
"""


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def save_run(model_name: str, sql_path: str, verdict: str, flags: list[str], report: dict[str, Any]) -> int:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO validation_runs (ts, model_name, sql_path, verdict, flags_json, report_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), model_name, sql_path, verdict, json.dumps(flags), json.dumps(report, default=str)),
        )
        conn.commit()
        return cur.lastrowid


def get_runs(limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM validation_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["flags"] = json.loads(d.pop("flags_json") or "[]")
            d["report"] = json.loads(d.pop("report_json") or "{}")
            out.append(d)
        return out


def get_run(run_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM validation_runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["flags"] = json.loads(d.pop("flags_json") or "[]")
        d["report"] = json.loads(d.pop("report_json") or "{}")
        return d
