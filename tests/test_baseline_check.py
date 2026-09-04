"""Unit tests for validation/baseline_check.py using synthetic in-memory
rows (fast, no database needed) - these check the statistics themselves,
independent of whether TPC-H data is available. End-to-end correctness
against real data is exercised by cli/demo.py run-demo and
scripts/verify_connection.py instead.
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validation.baseline_check import check_model_output
from validation.config import CUTOFF_MONTH


def _row(region: str, year: int, month: int, revenue: float) -> tuple:
    return (region, datetime.date(year, month, 1), revenue)


COLUMNS = ["region", "order_month", "total_revenue"]


def _stable_rows() -> list[tuple]:
    rows = []
    for year in (1995, 1996, 1997):
        for month in range(1, 13):
            for region in ("A", "B"):
                rows.append(_row(region, year, month, 100_000.0))
    return rows


def test_clean_output_is_verified():
    rows = _stable_rows()
    result = check_model_output(COLUMNS, rows, "order_month", "total_revenue", ["region"])
    assert result.verdict == "VERIFIED", result.flags
    assert result.duplicate_group_rows == 0


def test_duplicate_grouping_key_is_flagged():
    rows = _stable_rows()
    rows.append(rows[0])  # duplicate (region, order_month) pair
    result = check_model_output(COLUMNS, rows, "order_month", "total_revenue", ["region"])
    assert result.verdict == "FLAGGED"
    assert result.duplicate_group_rows == 1
    assert any("duplicate_grouping_keys" in f for f in result.flags)


def test_reconciliation_mismatch_is_flagged():
    rows = _stable_rows()
    reference_total = sum(r[2] for r in rows)
    inflated_rows = [(r[0], r[1], r[2] * 4) for r in rows]  # simulate a join fan-out
    result = check_model_output(
        COLUMNS, inflated_rows, "order_month", "total_revenue", ["region"], reference_total=reference_total
    )
    assert result.verdict == "FLAGGED"
    assert any("reconciliation_mismatch" in f for f in result.flags)
    assert result.reconciliation_delta_pct > 1.0


def test_reconciliation_within_tolerance_passes():
    rows = _stable_rows()
    reference_total = sum(r[2] for r in rows)
    result = check_model_output(
        COLUMNS, rows, "order_month", "total_revenue", ["region"], reference_total=reference_total
    )
    assert result.verdict == "VERIFIED"
    assert result.reconciliation_delta_pct == 0.0


def test_temporal_drift_flags_a_shifted_recent_mean():
    rows = []
    for year in (1995, 1996):
        for month in range(1, 13):
            rows.append(_row("A", year, month, 100_000.0))
    # recent period (>= CUTOFF_MONTH '1997-07') collapses to near-zero revenue
    for month in range(7, 13):
        rows.append(_row("A", 1997, month, 500.0))
    result = check_model_output(COLUMNS, rows, "order_month", "total_revenue", ["region"])
    assert result.verdict == "FLAGGED"
    assert any("distribution_drift" in f for f in result.flags)


def test_missing_column_raises():
    import pytest

    with pytest.raises(ValueError):
        check_model_output(COLUMNS, _stable_rows(), "not_a_column", "total_revenue", ["region"])
