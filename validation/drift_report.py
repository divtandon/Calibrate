"""Orchestrates one full validation pass: run the generated model for real,
compute its independent reconciliation control total, run baseline_check,
write any flags back through the governed flag_output_anomaly tool, and
persist the run for the dashboard. This is the function Phase 2's
checkpoint calls on both the correct model and the deliberately-broken one.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from db.connection import get_backend
from validation import results_store
from validation.baseline_check import check_model_output

# mcp_server.tools imports validation.config, which would create a circular
# import if this module imported mcp_server.tools eagerly - so those two
# tool calls are imported lazily inside run_and_report() instead.


def reference_orders_revenue_total() -> float:
    """Independent control total: SUM(o_totalprice) for every order that
    resolves through customer -> nation -> region, with no join to
    lineitem. Any correct revenue-sum model's grand total must reconcile
    to this number, regardless of how it buckets by dimension/period - a
    fan-out join (e.g. via lineitem) will not.
    """
    backend = get_backend()
    sql = f"""
        SELECT SUM(o.o_totalprice)
        FROM {backend.qualify('orders')} o
        JOIN {backend.qualify('customer')} c ON o.o_custkey = c.c_custkey
        JOIN {backend.qualify('nation')} n ON c.c_nationkey = n.n_nationkey
        JOIN {backend.qualify('region')} r ON n.n_regionkey = r.r_regionkey
    """
    _, rows = backend.execute(sql)
    return float(rows[0][0])


def reference_orders_count() -> float:
    """Independent control total for a COUNT-type metric: the real number
    of orders that resolve through customer -> nation -> region. A
    correct 'order count by X' model's grand total (summed across every
    dimension/period bucket) must equal this - a fan-out join would
    inflate it the same way it inflates a revenue sum.
    """
    backend = get_backend()
    sql = f"""
        SELECT COUNT(*)
        FROM {backend.qualify('orders')} o
        JOIN {backend.qualify('customer')} c ON o.o_custkey = c.c_custkey
        JOIN {backend.qualify('nation')} n ON c.c_nationkey = n.n_nationkey
        JOIN {backend.qualify('region')} r ON n.n_regionkey = r.r_regionkey
    """
    _, rows = backend.execute(sql)
    return float(rows[0][0])


def _detect_metric_aggregation(resolved_sql: str, metric_col: str) -> Optional[str]:
    """Reconciliation only makes sense when the reference total is the same
    kind of quantity as the model's metric - comparing a COUNT against a
    SUM-of-revenue reference is comparing apples to oranges, not catching a
    real bug (this is exactly what happened validating a real agent-
    generated "total order count by nation" model: its count of 1,500,000
    was actually correct, but got flagged as a 400%+ "mismatch" against the
    revenue reference). Sniff the aggregation function wrapping the column
    aliased as metric_col directly out of the resolved SQL so the right
    reference gets used instead.
    """
    pattern = rf"(sum|count|avg|min|max)\s*\([^()]*\)\s*as\s+{re.escape(metric_col)}\b"
    match = re.search(pattern, resolved_sql, re.IGNORECASE | re.DOTALL)
    return match.group(1).lower() if match else None


def run_and_report(
    model_name: str,
    sql: str,
    period_col: str,
    metric_col: str,
    dimension_cols: list[str],
    sql_path: Optional[str] = None,
    use_reconciliation: bool = True,
    actor: str = "validation-engine",
    source_note: Optional[str] = None,
) -> dict[str, Any]:
    from mcp_server.tools import flag_output_anomaly, run_generated_model

    exec_result = run_generated_model(sql, actor=actor)
    columns = exec_result["columns"]
    rows = exec_result["rows"]

    # A truncated result is a partial sum, not a real one - reconciling
    # against it would produce a "mismatch" that's really just missing
    # rows, not a join fan-out. Skip the check rather than report a
    # misleading reason.
    truncated = exec_result.get("truncated", False)
    reference_total: Optional[float] = None
    aggregation = None
    if use_reconciliation and not truncated:
        aggregation = _detect_metric_aggregation(exec_result["resolved_sql"], metric_col)
        if aggregation == "sum":
            reference_total = reference_orders_revenue_total()
        elif aggregation == "count":
            reference_total = reference_orders_count()
        # avg/min/max/unrecognized: no simple additive grand-total invariant
        # exists for those, so reconciliation is skipped rather than
        # compared against a reference that doesn't actually apply.

    result = check_model_output(
        columns=columns,
        rows=rows,
        period_col=period_col,
        metric_col=metric_col,
        dimension_cols=dimension_cols,
        reference_total=reference_total,
    )
    if truncated:
        result.flags.append(
            f"result_truncated: model returned more than {len(rows):,} rows and was truncated for validation - "
            f"reconciliation was skipped because a partial sum isn't a real total."
        )
        result.verdict = "FLAGGED"

    if result.verdict == "FLAGGED":
        for f in result.flags:
            flag_output_anomaly(model=model_name, note=f, severity="critical", actor=actor)

    report = {
        "model_name": model_name,
        "backend": exec_result["backend"],
        "resolved_sql": exec_result["resolved_sql"],
        "period_col": period_col,
        "metric_col": metric_col,
        "dimension_cols": dimension_cols,
        "source_note": source_note,
        "metric_aggregation": aggregation,
        **result.to_dict(),
    }
    results_store.save_run(model_name, sql_path or "", result.verdict, result.flags, report)
    return report
