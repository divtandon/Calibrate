"""Append-only audit trail. Every governed MCP tool call - allowed or
blocked - lands one row here. This is what the dashboard's governance strip
reads from, and it's the answer to "what did the agent actually do."
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

from common.state_db import connect as _connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_summary TEXT,
    allowed INTEGER NOT NULL,
    reason TEXT,
    duration_ms REAL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS anomaly_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    model TEXT NOT NULL,
    note TEXT,
    severity TEXT
);
"""


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _summarize_args(kwargs: dict[str, Any], max_len: int = 200) -> str:
    try:
        s = json.dumps(kwargs, default=str)
    except TypeError:
        s = str(kwargs)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


def log_call(
    actor: str,
    tool: str,
    args: dict[str, Any],
    allowed: bool,
    reason: str,
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, actor, tool, args_summary, allowed, reason, duration_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), actor, tool, _summarize_args(args), int(allowed), reason, duration_ms, error),
        )
        conn.commit()


def get_recent_calls(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def log_anomaly_flag(model: str, note: str, severity: str = "warning") -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO anomaly_flags (ts, model, note, severity) VALUES (?, ?, ?, ?)",
            (time.time(), model, note, severity),
        )
        conn.commit()


def get_recent_anomaly_flags(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM anomaly_flags ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
