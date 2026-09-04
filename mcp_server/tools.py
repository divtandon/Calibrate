"""The four MCP tools Calibrate exposes to the agent. Every function here is
wrapped with @governed(...), so every call - allowed or denied - is policy
checked and audit logged before it touches real data. These are plain
Python functions; mcp_server/server.py exposes them over the MCP protocol,
and agent/core.py's manual tool-use loop also calls them directly.
"""

from __future__ import annotations

from typing import Any, Optional

from db.connection import get_backend
from governance.guard import governed
from validation.config import BASELINE_END_EXCLUSIVE, BASELINE_START, RECENT_END_EXCLUSIVE


@governed("get_schema")
def get_schema(table: str) -> dict[str, Any]:
    """Return the real column list and row count for a TPC-H table on the
    active backend. This is what stops the agent from inventing a column
    that doesn't exist - it must look the table up first.
    """
    backend = get_backend()
    columns = backend.describe_table(table)
    _, count_rows = backend.execute(f"SELECT COUNT(*) FROM {backend.qualify(table)}")
    return {
        "table": table,
        "backend": backend.name,
        "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable} for c in columns],
        "row_count": count_rows[0][0],
    }


@governed("get_historical_baseline")
def get_historical_baseline(table: str, metric: str, period: str = "baseline") -> dict[str, Any]:
    """Real descriptive statistics for a numeric column on a TPC-H table,
    computed for either the historical 'baseline' period or the 'recent'
    period of real order-date history. Backs both agent grounding (so it
    knows realistic value ranges before generating SQL) and validation's
    before/after drift comparison.
    """
    backend = get_backend()
    if period not in ("baseline", "recent"):
        raise ValueError("period must be 'baseline' or 'recent'")

    date_col = "o_orderdate" if table.lower() == "orders" else None
    qualified = backend.qualify(table)

    where = ""
    if date_col:
        if period == "baseline":
            where = f"WHERE {date_col} >= DATE '{BASELINE_START}' AND {date_col} < DATE '{BASELINE_END_EXCLUSIVE}'"
        else:
            where = f"WHERE {date_col} >= DATE '{BASELINE_END_EXCLUSIVE}' AND {date_col} < DATE '{RECENT_END_EXCLUSIVE}'"

    sql = f"""
        SELECT
            COUNT(*) AS row_count,
            SUM(CASE WHEN {metric} IS NULL THEN 1 ELSE 0 END) AS null_count,
            AVG(CAST({metric} AS DOUBLE)) AS mean,
            STDDEV_POP(CAST({metric} AS DOUBLE)) AS stddev,
            MIN(CAST({metric} AS DOUBLE)) AS min,
            MAX(CAST({metric} AS DOUBLE)) AS max
        FROM {qualified}
        {where}
    """
    cols, rows = backend.execute(sql)
    row_count, null_count, mean, stddev, mn, mx = rows[0]
    return {
        "table": table,
        "metric": metric,
        "period": period,
        "backend": backend.name,
        "row_count": row_count,
        "null_rate": (null_count / row_count) if row_count else 0.0,
        "mean": mean,
        "stddev": stddev,
        "min": mn,
        "max": mx,
    }


def _resolve_dbt_sql(sql: str) -> str:
    """Minimal, dependency-free resolver for the subset of dbt Jinja our
    generated models use: {{ source('tpch', 'table') }} and a leading
    {{ config(...) }} line. Real dbt compilation happens separately via the
    `dbt` CLI (see scripts/materialize.py) - this fast path is what lets
    validation execute a generated model's SQL directly against the active
    backend without a full dbt compile/run cycle on every check.
    """
    import re

    backend = get_backend()

    def _sub_source(match: "re.Match") -> str:
        table = match.group(2)
        return backend.qualify(table)

    resolved = re.sub(
        r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}",
        _sub_source,
        sql,
    )
    resolved = re.sub(r"\{\{\s*config\([^)]*\)\s*\}\}", "", resolved)
    return resolved.strip()


@governed("run_generated_model")
def run_generated_model(sql: str, row_limit: int = 100_000) -> dict[str, Any]:
    """Execute a generated dbt model's SQL against the real backend and
    return its actual output. governance.policy blocks this before it runs
    if the SQL contains anything but a read (DROP/DELETE/INSERT/etc).
    """
    backend = get_backend()
    resolved = _resolve_dbt_sql(sql)
    columns, rows = backend.execute(resolved)
    truncated = len(rows) > row_limit
    if truncated:
        rows = rows[:row_limit]
    return {
        "backend": backend.name,
        "resolved_sql": resolved,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


@governed("flag_output_anomaly")
def flag_output_anomaly(model: str, note: str, severity: str = "warning") -> dict[str, Any]:
    """Write-back tool: validation calls this when a generated model fails
    baseline_check, so the flag itself goes through the same governed,
    audited path as every other tool call instead of being a silent
    Python-side return value.
    """
    from governance.audit_log import log_anomaly_flag

    log_anomaly_flag(model, note, severity)
    return {"model": model, "note": note, "severity": severity, "flagged": True}
