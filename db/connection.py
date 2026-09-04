"""Backend abstraction over the real data source Calibrate queries.

Two real implementations, selected by CALIBRATE_BACKEND:

- DuckDBBackend (default): loads the genuine TPC-H benchmark dataset via
  DuckDB's own `tpch` dbgen extension into a persistent local file. This is
  not a fixture or a mock - it's the same synthetic generator the TPC-H
  standard is built on, at scale factor 1 (1.5M orders / 6M lineitem rows),
  matching Snowflake's SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 exactly in shape.
- SnowflakeBackend: connects to a real Snowflake account's
  SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 database over the official connector.

Both expose the same DataBackend interface so everything above this layer
(mcp_server, validation, dbt profile target) is backend-agnostic.
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

TPCH_TABLES = ["region", "nation", "customer", "orders", "lineitem", "part", "partsupp", "supplier"]


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool


class DataBackend(ABC):
    name: str

    @abstractmethod
    def execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Run SQL, return (column_names, rows)."""

    @abstractmethod
    def qualify(self, table: str) -> str:
        """Fully-qualified name for a TPC-H table on this backend."""

    @abstractmethod
    def list_tables(self) -> list[str]:
        ...

    @abstractmethod
    def describe_table(self, table: str) -> list[ColumnInfo]:
        ...

    def close(self) -> None:  # pragma: no cover - optional override
        pass


class DuckDBBackend(DataBackend):
    name = "duckdb"

    def __init__(self, path: str | None = None):
        import duckdb

        self._duckdb = duckdb
        self.path = path or os.environ.get("DUCKDB_PATH", "data/calibrate.duckdb")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = duckdb.connect(self.path)
        self._ensure_data()

    def _ensure_data(self) -> None:
        existing = self._conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'tpch_sf1'"
        ).fetchall()
        have = {r[0] for r in existing}
        if have.issuperset(set(TPCH_TABLES)):
            return
        self._conn.execute("INSTALL tpch; LOAD tpch;")
        self._conn.execute("CREATE SCHEMA IF NOT EXISTS tpch_sf1;")
        self._conn.execute("CALL dbgen(sf=1, schema='tpch_sf1');")

    def execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        with self._lock:
            cur = self._conn.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            return cols, rows

    def qualify(self, table: str) -> str:
        return f"tpch_sf1.{table.lower()}"

    def list_tables(self) -> list[str]:
        cols, rows = self.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'tpch_sf1' ORDER BY 1"
        )
        return [r[0] for r in rows]

    def describe_table(self, table: str) -> list[ColumnInfo]:
        cols, rows = self.execute(f"DESCRIBE {self.qualify(table)}")
        # DuckDB DESCRIBE returns: column_name, column_type, null, key, default, extra
        return [ColumnInfo(name=r[0], type=r[1], nullable=(r[2] == "YES")) for r in rows]

    def close(self) -> None:
        self._conn.close()


class SnowflakeBackend(DataBackend):
    name = "snowflake"

    def __init__(self):
        import snowflake.connector

        self.database = os.environ.get("SNOWFLAKE_DATABASE", "SNOWFLAKE_SAMPLE_DATA")
        self.schema = os.environ.get("SNOWFLAKE_SCHEMA", "TPCH_SF1")
        required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                f"CALIBRATE_BACKEND=snowflake but missing env vars: {', '.join(missing)}. "
                "Set them in .env (see .env.example) or switch CALIBRATE_BACKEND=duckdb."
            )
        self._lock = threading.Lock()
        self._conn = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            role=os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
            database=self.database,
            schema=self.schema,
        )

    def execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                return cols, rows
            finally:
                cur.close()

    def qualify(self, table: str) -> str:
        return f"{self.database}.{self.schema}.{table.upper()}"

    def list_tables(self) -> list[str]:
        cols, rows = self.execute(
            f"SELECT table_name FROM {self.database}.information_schema.tables "
            f"WHERE table_schema = '{self.schema}' ORDER BY 1"
        )
        return [r[0] for r in rows]

    def describe_table(self, table: str) -> list[ColumnInfo]:
        cols, rows = self.execute(f"DESCRIBE TABLE {self.qualify(table)}")
        # Snowflake DESCRIBE TABLE returns: name, type, kind, null?, default, ...
        return [ColumnInfo(name=r[0], type=r[1], nullable=(r[3] == "Y")) for r in rows]

    def close(self) -> None:
        self._conn.close()


_backend_instance: DataBackend | None = None
_backend_lock = threading.Lock()


def get_backend() -> DataBackend:
    global _backend_instance
    with _backend_lock:
        if _backend_instance is not None:
            return _backend_instance
        kind = os.environ.get("CALIBRATE_BACKEND", "duckdb").lower()
        if kind == "snowflake":
            _backend_instance = SnowflakeBackend()
        elif kind == "duckdb":
            _backend_instance = DuckDBBackend()
        else:
            raise ValueError(f"Unknown CALIBRATE_BACKEND '{kind}' - use 'duckdb' or 'snowflake'.")
        return _backend_instance


def reset_backend() -> None:
    """Testing hook: force the next get_backend() call to reconnect."""
    global _backend_instance
    with _backend_lock:
        if _backend_instance is not None:
            _backend_instance.close()
        _backend_instance = None
