"""Orchestrates one full validation pass: run the generated model for real,
compute its independent reconciliation control total, run baseline_check,
write any flags back through the governed flag_output_anomaly tool, and
persist the run for the dashboard. This is the function Phase 2's
checkpoint calls on both the correct model and the deliberately-broken one.
"""

from __future__ import annotations

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
    lineitem. Any correct 'revenue by region' style model's grand total
    must reconcile to this number, regardless of how it buckets by
    region/month - a fan-out join (e.g. via lineitem) will not.
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


def run_and_report(
    model_name: str,
    sql: str,
    period_col: str,
    metric_col: str,
    dimension_cols: list[str],
    sql_path: Optional[str] = None,
    use_reconciliation: bool = True,
    actor: str = "validation-engine",
) -> dict[str, Any]:
    from mcp_server.tools import flag_output_anomaly, run_generated_model

    exec_result = run_generated_model(sql, actor=actor)
    columns = exec_result["columns"]
    rows = exec_result["rows"]

    reference_total = reference_orders_revenue_total() if use_reconciliation else None

    result = check_model_output(
        columns=columns,
        rows=rows,
        period_col=period_col,
        metric_col=metric_col,
        dimension_cols=dimension_cols,
        reference_total=reference_total,
    )

    if result.verdict == "FLAGGED":
        for f in result.flags:
            flag_output_anomaly(model=model_name, note=f, severity="critical", actor=actor)

    report = {
        "model_name": model_name,
        "backend": exec_result["backend"],
        "resolved_sql": exec_result["resolved_sql"],
        **result.to_dict(),
    }
    results_store.save_run(model_name, sql_path or "", result.verdict, result.flags, report)
    return report
