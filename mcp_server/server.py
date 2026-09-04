"""Calibrate's real MCP server. Exposes get_schema, get_historical_baseline,
run_generated_model, and flag_output_anomaly as MCP tools over stdio, so any
MCP client - Claude Desktop, Claude Code, agent/core.py's own client, or a
third party - can connect to it the same way. Every call still goes through
governance/guard.py before it touches data; the MCP layer is transport, not
a bypass.

Run directly for a standalone server:
    python -m mcp_server.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server import tools as t

mcp = FastMCP("calibrate")


@mcp.tool()
def get_schema(table: str, actor: str = "calibrate-agent") -> dict[str, Any]:
    """Return the real columns and row count for a TPC-H table (orders, customer, nation, region, lineitem, part, partsupp, supplier)."""
    return t.get_schema(table, actor=actor)


@mcp.tool()
def get_historical_baseline(table: str, metric: str, period: str = "baseline", actor: str = "calibrate-agent") -> dict[str, Any]:
    """Real descriptive statistics (row count, null rate, mean, stddev, min, max) for a numeric column, over either the 'baseline' or 'recent' order-date period."""
    return t.get_historical_baseline(table, metric, period, actor=actor)


@mcp.tool()
def run_generated_model(sql: str, actor: str = "calibrate-agent") -> dict[str, Any]:
    """Execute a generated dbt model's SQL (using {{ source('tpch', table) }} refs) against the real backend and return its actual output."""
    return t.run_generated_model(sql, actor=actor)


@mcp.tool()
def flag_output_anomaly(model: str, note: str, severity: str = "warning", actor: str = "calibrate-agent") -> dict[str, Any]:
    """Write-back a validation failure against a model so it is recorded in the governed audit trail."""
    return t.flag_output_anomaly(model, note, severity, actor=actor)


if __name__ == "__main__":
    mcp.run()
