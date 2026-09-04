"""Single shared SQLite file backing the audit log, anomaly flags, and
validation run history. One file, one connection helper, so the dashboard
can read all three without juggling paths.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def state_db_path() -> Path:
    return Path(os.environ.get("CALIBRATE_STATE_DB", "data/calibrate_state.db"))


@contextmanager
def connect():
    path = state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        yield conn
    finally:
        conn.close()
