"""Phase 0 checkpoint, standalone: connect to the active backend and prove a
real query returns real rows before anything else runs. This is the exact
gate PROJECT_SPEC.md section 7's kickoff prompt describes - "don't proceed
past this until that query returns real rows."

    python scripts/verify_connection.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    from db.connection import get_backend

    backend = get_backend()
    print(f"Connected. backend={backend.name}")

    tables = backend.list_tables()
    print(f"Tables visible: {tables}")
    assert "orders" in tables, "orders table not found - backend is not pointed at TPC-H data"

    cols, rows = backend.execute(f"SELECT * FROM {backend.qualify('orders')} ORDER BY o_orderkey LIMIT 3")
    print(f"\nSELECT * FROM {backend.qualify('orders')} LIMIT 3:")
    print("  " + " | ".join(cols))
    for row in rows:
        print("  " + " | ".join(str(v) for v in row))

    _, count_rows = backend.execute(f"SELECT COUNT(*) FROM {backend.qualify('orders')}")
    total = count_rows[0][0]
    print(f"\nTotal real rows in orders: {total:,}")
    assert total > 0, "orders table is empty"

    print("\nPhase 0 checkpoint passed: real tool call, real schema, real data.")


if __name__ == "__main__":
    main()
