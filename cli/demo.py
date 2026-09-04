"""Calibrate's command-line demo. Run `python -m cli.demo --help` for the
full list of subcommands. This is the same "cli / demo" box at the top of
PROJECT_SPEC.md's architecture diagram - every subcommand here calls the
real governed tools and validation layer underneath, nothing here is a
separate mocked path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make stdout/stderr UTF-8 regardless of the host console codepage (Windows
# defaults to cp1252, which can't print the sigma in drift reports).
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()


def cmd_setup(args: argparse.Namespace) -> None:
    import os

    if os.environ.get("CALIBRATE_BACKEND", "duckdb") == "duckdb":
        from scripts.setup_local_data import main as setup_local_data

        setup_local_data()
        print()

    from db.connection import get_backend

    backend = get_backend()
    tables = backend.list_tables()
    print(f"[setup] backend={backend.name} tables={tables}")
    for table in tables:
        _, rows = backend.execute(f"SELECT COUNT(*) FROM {backend.qualify(table)}")
        print(f"  {table:<10} {rows[0][0]:>10,} rows")


def cmd_schema(args: argparse.Namespace) -> None:
    from mcp_server.tools import get_schema

    result = get_schema(args.table, actor="demo-cli")
    print(json.dumps(result, indent=2, default=str))


def cmd_baseline(args: argparse.Namespace) -> None:
    from mcp_server.tools import get_historical_baseline

    result = get_historical_baseline(args.table, args.metric, args.period, actor="demo-cli")
    print(json.dumps(result, indent=2, default=str))


def cmd_generate(args: argparse.Namespace) -> None:
    from agent.core import generate_model

    print(f"[generate] prompt: {args.prompt!r}")
    result = generate_model(args.prompt)
    print(f"[generate] saved {result.model_name}.sql to examples/ after {result.turns} turn(s), "
          f"{len(result.tool_calls)} governed tool call(s):")
    for call in result.tool_calls:
        marker = "ERROR" if call["error"] else "ok"
        print(f"  - {call['tool']}({call['input']}) [{marker}]")
    print()
    print(result.sql)


def cmd_validate(args: argparse.Namespace) -> None:
    from validation.drift_report import run_and_report

    sql = Path(args.sql_path).read_text(encoding="utf-8")
    dims = args.dimension_cols.split(",") if args.dimension_cols else ["region"]
    report = run_and_report(
        model_name=args.model_name or Path(args.sql_path).stem,
        sql=sql,
        period_col=args.period_col,
        metric_col=args.metric_col,
        dimension_cols=dims,
        sql_path=args.sql_path,
        use_reconciliation=not args.no_reconciliation,
    )
    print(f"[validate] {report['model_name']}: {report['verdict']}")
    for flag in report["flags"]:
        print(f"  - {flag}")
    if not report["flags"]:
        print("  no flags - output matches expected shape and historical distribution")
    print()
    print(json.dumps(report, indent=2, default=str))


def cmd_run_demo(args: argparse.Namespace) -> None:
    """Phase 0-2 checkpoint in one command: real schema call, real baseline
    call, then validate the correct example and the deliberately-broken one.
    """
    from mcp_server.tools import get_schema, get_historical_baseline
    from validation.drift_report import run_and_report

    print("=== Phase 0: real tool call -> real schema ===")
    schema = get_schema("orders", actor="demo-cli")
    print(f"orders: {len(schema['columns'])} columns, {schema['row_count']:,} rows (backend={schema['backend']})")

    print()
    print("=== Phase 0: real tool call -> real baseline ===")
    baseline = get_historical_baseline("orders", "o_totalprice", "baseline", actor="demo-cli")
    print(f"o_totalprice baseline period: {baseline['row_count']:,} rows, mean={baseline['mean']:.2f}, "
          f"stddev={baseline['stddev']:.2f}")

    print()
    print("=== Phase 2: validate the correct generated model ===")
    good_sql = Path("examples/monthly_revenue_by_region.sql").read_text(encoding="utf-8")
    good = run_and_report(
        "monthly_revenue_by_region", good_sql, period_col="order_month", metric_col="total_revenue",
        dimension_cols=["region"], sql_path="examples/monthly_revenue_by_region.sql",
    )
    print(f"verdict: {good['verdict']}  (model total {good['model_total']:,.2f} vs reference "
          f"{good['reference_total']:,.2f})")

    print()
    print("=== Phase 2: validate the deliberately-broken model ===")
    bad_sql = Path("examples/monthly_revenue_by_region_broken.sql").read_text(encoding="utf-8")
    bad = run_and_report(
        "monthly_revenue_by_region_broken", bad_sql, period_col="order_month", metric_col="total_revenue",
        dimension_cols=["region"], sql_path="examples/monthly_revenue_by_region_broken.sql",
    )
    print(f"verdict: {bad['verdict']}")
    for flag in bad["flags"]:
        print(f"  - {flag}")


def cmd_audit(args: argparse.Namespace) -> None:
    from governance.audit_log import get_recent_calls

    for row in get_recent_calls(args.limit):
        status = "ALLOW" if row["allowed"] else "DENY"
        print(f"[{status}] {row['actor']:<18} {row['tool']:<24} {row.get('duration_ms') or 0:>7.2f}ms  {row['reason']}")


def cmd_mcp_serve(args: argparse.Namespace) -> None:
    from mcp_server.server import mcp

    mcp.run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calibrate", description="Calibrate demo CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="Connect to the backend and confirm TPC-H data is queryable")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("schema", help="Real get_schema tool call")
    p.add_argument("table")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("baseline", help="Real get_historical_baseline tool call")
    p.add_argument("table")
    p.add_argument("metric")
    p.add_argument("--period", choices=["baseline", "recent"], default="baseline")
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("generate", help="Agent: natural language -> real dbt model (requires ANTHROPIC_API_KEY)")
    p.add_argument("prompt")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("validate", help="Run baseline_check against a generated model's SQL file")
    p.add_argument("sql_path")
    p.add_argument("--model-name")
    p.add_argument("--period-col", default="order_month")
    p.add_argument("--metric-col", default="total_revenue")
    p.add_argument("--dimension-cols", default="region", help="comma-separated")
    p.add_argument("--no-reconciliation", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("run-demo", help="Full Phase 0-2 checkpoint: real schema, real baseline, both example models")
    p.set_defaults(func=cmd_run_demo)

    p = sub.add_parser("audit", help="Show recent governed tool calls")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("mcp-serve", help="Run the real MCP server over stdio")
    p.set_defaults(func=cmd_mcp_serve)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
