"""The single writer for the local DuckDB TPC-H dataset. Run this once
before anything else touches the duckdb backend:

    python scripts/setup_local_data.py

Runtime code (db/connection.py's DuckDBBackend) opens the file read_only -
it never writes, which is both a concurrency fix (multiple CLI/dashboard/
test processes can safely open read-only connections at once, but exactly
one process should ever populate the data) and a defense-in-depth
governance property: even a bug in the SQL-keyword policy filter couldn't
mutate this database at the connection level.

Verifies exact row counts against the known scale-factor-1 TPC-H shape
rather than just "do the tables exist" - a partial or doubled generation
(which is exactly what happened once during development: a race between
two writable connections left customer/nation/region at 2x their correct
row counts while orders/lineitem stayed correct) gets detected and the
schema is dropped and regenerated clean.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

# Exact TPC-H scale-factor-1 row counts (fixed by the benchmark's own
# specification, not derived from a run) - what a clean generation must match.
EXPECTED_COUNTS = {
    "region": 5,
    "nation": 25,
    "customer": 150_000,
    "supplier": 10_000,
    "part": 200_000,
    "partsupp": 800_000,
    "orders": 1_500_000,
    "lineitem": 6_001_215,
}


def main() -> None:
    import duckdb

    path = os.environ.get("DUCKDB_PATH", "data/calibrate.duckdb")
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(path)
    try:
        con.execute("INSTALL tpch; LOAD tpch;")
        con.execute("CREATE SCHEMA IF NOT EXISTS tpch_sf1;")

        existing = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'tpch_sf1'"
            ).fetchall()
        }

        counts: dict[str, int] = {}
        for table in EXPECTED_COUNTS:
            if table in existing:
                counts[table] = con.execute(f"SELECT COUNT(*) FROM tpch_sf1.{table}").fetchone()[0]

        clean = counts == EXPECTED_COUNTS
        if clean:
            print(f"tpch_sf1 already holds a clean TPC-H SF1 dataset at {path} - nothing to do.")
        else:
            if counts:
                print(f"tpch_sf1 exists but doesn't match expected SF1 counts ({counts}) - regenerating clean.")
                con.execute("DROP SCHEMA tpch_sf1 CASCADE;")
                con.execute("CREATE SCHEMA tpch_sf1;")
            print("Generating real TPC-H benchmark data (scale factor 1) via DuckDB's tpch extension...")
            con.execute("CALL dbgen(sf=1, schema='tpch_sf1');")

        final_counts = {
            table: con.execute(f"SELECT COUNT(*) FROM tpch_sf1.{table}").fetchone()[0] for table in EXPECTED_COUNTS
        }
        for table, expected in EXPECTED_COUNTS.items():
            actual = final_counts[table]
            status = "OK" if actual == expected else "MISMATCH"
            print(f"  {table:<10} {actual:>10,} rows  [{status}]")
        assert final_counts == EXPECTED_COUNTS, f"Data setup did not produce the expected TPC-H SF1 shape: {final_counts}"
    finally:
        con.close()

    print(f"\nLocal TPC-H SF1 data ready at {path}. Runtime connections are read-only from here.")


if __name__ == "__main__":
    main()
