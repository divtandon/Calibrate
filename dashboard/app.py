"""Calibrate's dashboard. Reads real state - validation_runs and audit_log
from data/calibrate_state.db - and can trigger real runs (generate,
validate, the full Phase 0-2 demo) live from the browser via NiceGUI.
Nothing on this page is sample/placeholder data once at least one demo run
has happened; before that, panels say so explicitly instead of faking it.

Run with: python -m dashboard.app
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from nicegui import run, ui

load_dotenv()

BG = "#0a0d12"
PANEL = "#12161f"
PANEL_BORDER = "#232838"
TEXT = "#e6e9f0"
MUTED = "#7d879b"
ACCENT = "#3ecf8e"
ACCENT_DIM = "#1f6b4d"
DANGER = "#ef5a6f"
DANGER_DIM = "#5c2430"
AMBER = "#f0b429"
TEAL = "#2dd4bf"
BASELINE_COLOR = "#6b7690"

state: dict[str, Any] = {"selected_run_id": None}


def _backend_label() -> str:
    kind = os.environ.get("CALIBRATE_BACKEND", "duckdb")
    if kind == "snowflake":
        db = os.environ.get("SNOWFLAKE_DATABASE", "SNOWFLAKE_SAMPLE_DATA")
        schema = os.environ.get("SNOWFLAKE_SCHEMA", "TPCH_SF1")
        return f"SNOWFLAKE: {db}.{schema}"
    return "DUCKDB: tpch_sf1 (local TPC-H SF1)"


def _fmt_pct(x: Optional[float]) -> str:
    return f"{x:+.2%}" if x is not None else "—"


def _fmt_sigma(x: Optional[float]) -> str:
    return f"{x:+.2f}σ" if x is not None else "—"


def _verdict_color(verdict: str) -> str:
    return ACCENT if verdict == "VERIFIED" else DANGER


# ---------------------------------------------------------------- styling --

def _inject_style() -> None:
    ui.add_head_html(f"""
    <style>
      body {{ background: {BG}; }}
      .q-page {{ background: {BG}; }}
      * {{ font-family: 'JetBrains Mono', 'Cascadia Code', ui-monospace, monospace; }}
      .cal-panel {{
        background: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 10px;
      }}
      .cal-badge {{
        border-radius: 5px; padding: 2px 9px; font-size: 11px; font-weight: 700;
        letter-spacing: .04em;
      }}
      .cal-row-flash {{ animation: cal-flash 1.1s ease-out; }}
      @keyframes cal-flash {{
        0% {{ background: rgba(62,207,142,0.22); }}
        100% {{ background: transparent; }}
      }}
      .cal-pulse {{
        display:inline-block; width:8px; height:8px; border-radius:50%;
        background: {ACCENT}; box-shadow: 0 0 0 rgba(62,207,142,0.5);
        animation: cal-pulse 2s infinite;
      }}
      @keyframes cal-pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba(62,207,142,0.55); }}
        70% {{ box-shadow: 0 0 0 8px rgba(62,207,142,0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(62,207,142,0); }}
      }}
      ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
      ::-webkit-scrollbar-thumb {{ background: {PANEL_BORDER}; border-radius: 4px; }}
    </style>
    """)


def badge(text: str, color: str, bg: str) -> ui.element:
    return ui.label(text).classes("cal-badge").style(f"color:{color}; background:{bg};")


# --------------------------------------------------------------- data i/o --

def load_runs(limit: int = 100) -> list[dict[str, Any]]:
    from validation import results_store

    return results_store.get_runs(limit)


def load_audit(limit: int = 40) -> list[dict[str, Any]]:
    from governance.audit_log import get_recent_calls

    return get_recent_calls(limit)


# ------------------------------------------------------------------- app --

@ui.page("/")
def index() -> None:
    _inject_style()
    ui.dark_mode().enable()
    ui.colors(primary=ACCENT)

    with ui.column().classes("w-full h-screen no-wrap").style(f"background:{BG}; padding:0; gap:0;"):
        _header()
        with ui.row().classes("w-full flex-grow no-wrap").style("gap:0; min-height:0;"):
            with ui.column().style(
                f"width:300px; min-width:300px; height:100%; background:{PANEL}; "
                f"border-right:1px solid {PANEL_BORDER}; padding:14px; gap:14px; overflow-y:auto;"
            ):
                _sidebar()
            with ui.column().classes("flex-grow").style("height:100%; padding:18px; gap:14px; overflow-y:auto;"):
                _main_panel.refresh()
        _governance_strip()


def _header() -> None:
    with ui.row().classes("w-full items-center no-wrap").style(
        f"background:{PANEL}; border-bottom:1px solid {PANEL_BORDER}; padding:10px 20px; gap:18px;"
    ):
        ui.icon("hub", size="22px").style(f"color:{ACCENT};")
        with ui.column().style("gap:0;"):
            ui.label("Calibrate").style(f"font-size:16px; font-weight:800; color:{TEXT}; letter-spacing:.02em;")
            ui.label("Data Engine").style(f"font-size:10px; color:{MUTED};")
        ui.space()
        ui.label("TARGET:").style(f"font-size:11px; color:{MUTED};")
        ui.label(_backend_label()).style(f"font-size:12px; color:{TEAL}; font-weight:600;")
        with ui.row().classes("items-center").style("gap:6px;"):
            ui.html('<span class="cal-pulse"></span>')
            ui.label("ACTIVE").style(f"font-size:11px; color:{ACCENT}; font-weight:700;")
        clock = ui.label().style(f"font-size:12px; color:{MUTED}; min-width:150px;")
        ui.timer(1.0, lambda: clock.set_text(f"telemetry live · {datetime.datetime.now().strftime('%H:%M:%S')}"))


def _sidebar() -> None:
    ui.label("PIPELINE CATALOG").style(f"font-size:11px; font-weight:700; color:{MUTED}; letter-spacing:.08em;")
    search = ui.input(placeholder="Search models...").props("dense outlined dark").classes("w-full").style(
        f"--q-primary:{ACCENT};"
    )
    _catalog_list.refresh(search.value if search else "")
    search.on("update:model-value", lambda e: _catalog_list.refresh(e.args or ""))

    ui.separator().style(f"background:{PANEL_BORDER};")
    ui.label("NEW GENERATION").style(f"font-size:11px; font-weight:700; color:{MUTED}; letter-spacing:.08em;")
    prompt = ui.textarea(placeholder="e.g. generate a dbt model for monthly revenue by region").props(
        "dense outlined dark rows=3"
    ).classes("w-full")
    gen_status = ui.label("").style(f"font-size:11px; color:{MUTED};")

    async def do_generate() -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            gen_status.set_text("ANTHROPIC_API_KEY not set - see .env.example")
            gen_status.style(f"color:{DANGER};")
            return
        gen_status.set_text("agent running: calling get_schema, get_historical_baseline, run_generated_model...")
        gen_status.style(f"color:{AMBER};")
        try:
            from agent.core import generate_model

            result = await run.io_bound(generate_model, prompt.value)
            gen_status.set_text(f"saved examples/{result.model_name}.sql - validating...")
            from validation.drift_report import run_and_report

            await run.io_bound(
                run_and_report,
                result.model_name,
                result.sql,
                "order_month",
                "total_revenue",
                ["region"],
                f"examples/{result.model_name}.sql",
            )
            gen_status.set_text("done - see catalog")
            gen_status.style(f"color:{ACCENT};")
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
            gen_status.set_text(f"error: {exc}")
            gen_status.style(f"color:{DANGER};")
        _catalog_list.refresh("")

    ui.button("Generate + Validate", on_click=do_generate).props("unelevated").classes("w-full").style(
        f"background:{ACCENT}; color:#04120c; font-weight:700;"
    )

    ui.separator().style(f"background:{PANEL_BORDER};")

    async def do_full_demo() -> None:
        gen_status.set_text("running Phase 0-2 checkpoint on the two example models...")
        gen_status.style(f"color:{AMBER};")
        from validation.drift_report import run_and_report

        for name, path in [
            ("monthly_revenue_by_region", "examples/monthly_revenue_by_region.sql"),
            ("monthly_revenue_by_region_broken", "examples/monthly_revenue_by_region_broken.sql"),
        ]:
            sql = Path(path).read_text(encoding="utf-8")
            await run.io_bound(run_and_report, name, sql, "order_month", "total_revenue", ["region"], path)
        gen_status.set_text("done - see catalog")
        gen_status.style(f"color:{ACCENT};")
        _catalog_list.refresh("")

    ui.button("Run Phase 0-2 Demo", on_click=do_full_demo).props("outline").classes("w-full").style(
        f"color:{TEAL}; border-color:{TEAL};"
    )


@ui.refreshable
def _catalog_list(filter_text: str = "") -> None:
    runs = load_runs()
    seen_models: dict[str, dict[str, Any]] = {}
    for r in runs:
        seen_models.setdefault(r["model_name"], r)  # newest first already
    filter_text = (filter_text or "").lower()

    if not seen_models:
        ui.label("No runs yet - click 'Run Phase 0-2 Demo' below.").style(f"font-size:12px; color:{MUTED};")
        return

    with ui.column().classes("w-full").style("gap:6px;"):
        for model_name, r in seen_models.items():
            if filter_text and filter_text not in model_name.lower():
                continue
            is_selected = state["selected_run_id"] == r["id"]
            border = ACCENT if is_selected else PANEL_BORDER
            with ui.row().classes("w-full items-center cursor-pointer no-wrap").style(
                f"border:1px solid {border}; border-radius:8px; padding:8px 10px; gap:8px;"
            ).on("click", lambda r=r: _select_run(r["id"])):
                with ui.column().style("gap:0; min-width:0; flex-grow:1;"):
                    ui.label(f"{model_name}.sql").style(
                        f"font-size:12px; color:{TEXT}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                    )
                    ui.label(r["sql_path"] or "").style(
                        f"font-size:10px; color:{MUTED}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                    )
                if r["verdict"] == "VERIFIED":
                    badge("VERIFIED", "#04120c", ACCENT)
                else:
                    badge("FLAGGED", "#2a0a10", DANGER)


def _select_run(run_id: int) -> None:
    state["selected_run_id"] = run_id
    _catalog_list.refresh("")
    _main_panel.refresh()


@ui.refreshable
def _main_panel() -> None:
    from validation import results_store

    run_id = state["selected_run_id"]
    if run_id is None:
        runs = load_runs(1)
        run_id = runs[0]["id"] if runs else None
        state["selected_run_id"] = run_id

    if run_id is None:
        with ui.column().classes("w-full items-center justify-center").style("height:60vh; gap:8px;"):
            ui.icon("insights", size="48px").style(f"color:{PANEL_BORDER};")
            ui.label("No validation runs yet").style(f"font-size:14px; color:{MUTED};")
            ui.label("Run the Phase 0-2 demo from the sidebar to see real numbers here.").style(
                f"font-size:12px; color:{MUTED};"
            )
        return

    run = results_store.get_run(run_id)
    if run is None:
        return
    report = run["report"]

    ui.label(f"Dashboards  ›  {run['model_name']}").style(f"font-size:11px; color:{MUTED};")

    with ui.row().classes("w-full no-wrap").style("gap:14px;"):
        _stat_tile("ROW COUNT DELTA", _fmt_pct(report.get("row_count_delta_pct")), report.get("row_count_delta_pct"), 0.25)
        _stat_tile("NULL RATE VARIANCE", _fmt_pct(report.get("null_rate_variance")), report.get("null_rate_variance"), 0.02)
        _stat_tile("DISTRIBUTION DRIFT", _fmt_sigma(report.get("distribution_drift_sigma")), report.get("distribution_drift_sigma"), 3.0)
        with ui.column().classes("cal-panel").style("padding:14px; gap:2px; min-width:170px;"):
            ui.label("RECONCILIATION").style(f"font-size:10px; color:{MUTED}; letter-spacing:.06em;")
            delta = report.get("reconciliation_delta_pct")
            color = DANGER if (delta or 0) > 0.005 else ACCENT
            ui.label(_fmt_pct(delta) if delta is not None else "skipped").style(
                f"font-size:20px; font-weight:800; color:{color};"
            )
            ui.label(f"model {report.get('model_total', 0):,.0f} vs ref {report.get('reference_total', 0):,.0f}").style(
                f"font-size:10px; color:{MUTED};"
            )

    with ui.column().classes("cal-panel w-full").style("padding:14px; gap:8px;"):
        with ui.row().classes("items-center w-full"):
            ui.label("Generated Model Output vs Historical Baseline").style(f"font-size:12px; font-weight:700; color:{TEXT};")
            ui.space()
            verdict = run["verdict"]
            badge(verdict, "#04120c" if verdict == "VERIFIED" else "#2a0a10", ACCENT if verdict == "VERIFIED" else DANGER)
        _drift_chart(report)

    if run["flags"]:
        with ui.column().classes("w-full").style("gap:6px;"):
            for flag in run["flags"]:
                with ui.row().classes("w-full no-wrap").style(
                    f"border:1px solid {DANGER}; background:{DANGER_DIM}22; border-radius:8px; padding:10px 12px; gap:8px;"
                ):
                    ui.icon("warning", size="16px").style(f"color:{DANGER};")
                    ui.label(flag).style(f"font-size:12px; color:{TEXT}; white-space:pre-wrap;")

    with ui.expansion("Resolved SQL", icon="code").classes("w-full cal-panel").style(f"color:{TEXT};"):
        ui.code(report.get("resolved_sql", ""), language="sql").classes("w-full")


def _stat_tile(label: str, value_text: str, raw_value: Optional[float], threshold: float) -> None:
    flagged = raw_value is not None and abs(raw_value) > threshold
    color = DANGER if flagged else ACCENT
    with ui.column().classes("cal-panel").style("padding:14px; gap:2px; min-width:170px;"):
        ui.label(label).style(f"font-size:10px; color:{MUTED}; letter-spacing:.06em;")
        ui.label(value_text).style(f"font-size:20px; font-weight:800; color:{color};")
        if flagged:
            badge("FLAGGED", "#2a0a10", DANGER)
        else:
            ui.label("within tolerance").style(f"font-size:10px; color:{MUTED};")


def _drift_chart(report: dict[str, Any]) -> None:
    from validation.config import CUTOFF_MONTH

    resolved_sql = report.get("resolved_sql", "")
    baseline = report.get("baseline") or {}
    recent = report.get("recent") or {}

    # Re-run the resolved SQL live so the chart reflects the exact rows the
    # verdict was computed from - no separately-maintained chart dataset.
    try:
        from db.connection import get_backend

        backend = get_backend()
        cols, rows = backend.execute(resolved_sql)
        idx = {c: i for i, c in enumerate(cols)}
    except Exception:
        cols, rows, idx = [], [], {}

    period_col, metric_col = "order_month", "total_revenue"
    series: dict[str, float] = {}
    if period_col in idx and metric_col in idx:
        for r in rows:
            p = r[idx[period_col]]
            key = f"{p.year:04d}-{p.month:02d}" if hasattr(p, "year") else str(p)[:7]
            series[key] = series.get(key, 0.0) + float(r[idx[metric_col]] or 0)

    periods = sorted(series.keys())
    values = [series[p] for p in periods]
    baseline_vals = [v if p < CUTOFF_MONTH else None for p, v in zip(periods, values)]
    recent_vals = [v if p >= CUTOFF_MONTH else None for p, v in zip(periods, values)]

    flagged_recent = bool(report.get("flags"))

    options = {
        "backgroundColor": "transparent",
        "textStyle": {"color": TEXT, "fontFamily": "monospace"},
        "grid": {"left": 60, "right": 24, "top": 24, "bottom": 40},
        "xAxis": {
            "type": "category", "data": periods, "boundaryGap": False,
            "axisLine": {"lineStyle": {"color": PANEL_BORDER}},
            "axisLabel": {"color": MUTED, "fontSize": 9},
        },
        "yAxis": {
            "type": "value",
            "axisLine": {"show": False},
            "splitLine": {"lineStyle": {"color": PANEL_BORDER}},
            "axisLabel": {"color": MUTED, "fontSize": 9, "formatter": "{value}"},
        },
        "tooltip": {"trigger": "axis", "backgroundColor": PANEL, "borderColor": PANEL_BORDER, "textStyle": {"color": TEXT}},
        "series": [
            {
                "name": "Historical baseline",
                "type": "line", "data": baseline_vals, "smooth": True, "symbol": "none",
                "lineStyle": {"color": BASELINE_COLOR, "width": 2},
                "areaStyle": {"color": BASELINE_COLOR, "opacity": 0.12},
                "connectNulls": False,
                "animationDuration": 900,
            },
            {
                "name": "Generated model output",
                "type": "line", "data": recent_vals, "smooth": True, "symbol": "circle", "symbolSize": 5,
                "lineStyle": {"color": DANGER if flagged_recent else TEAL, "width": 2.5},
                "areaStyle": {"color": DANGER if flagged_recent else TEAL, "opacity": 0.16},
                "connectNulls": False,
                "animationDuration": 1400,
                "animationEasing": "elasticOut",
            },
        ],
        "legend": {"data": ["Historical baseline", "Generated model output"], "textStyle": {"color": MUTED, "fontSize": 10}, "top": 0, "right": 0},
    }
    ui.echart(options).classes("w-full").style("height: 320px;")


def _governance_strip() -> None:
    with ui.column().style(
        f"background:{PANEL}; border-top:1px solid {PANEL_BORDER}; padding:8px 16px; gap:4px; max-height:150px;"
    ):
        with ui.row().classes("items-center"):
            ui.icon("shield", size="14px").style(f"color:{TEAL};")
            ui.label("GOVERNANCE — live audit trail").style(f"font-size:10px; font-weight:700; color:{MUTED}; letter-spacing:.06em;")
        rows_container = ui.row().classes("w-full no-wrap").style("gap:6px; overflow-x:auto; padding-bottom:4px;")
        seen_ids: set[int] = set()

        def refresh_strip() -> None:
            calls = load_audit(15)
            rows_container.clear()
            with rows_container:
                for c in reversed(calls):
                    new = c["id"] not in seen_ids
                    seen_ids.add(c["id"])
                    ok = bool(c["allowed"])
                    color = ACCENT if ok else DANGER
                    cls = "cal-row-flash" if new else ""
                    with ui.column().classes(f"{cls}").style(
                        f"border:1px solid {PANEL_BORDER}; border-left:3px solid {color}; border-radius:6px; "
                        f"padding:5px 9px; min-width:150px; gap:0;"
                    ):
                        ui.label(f"{c['actor']} → {c['tool']}").style(f"font-size:10px; color:{TEXT};")
                        ui.label(f"{'allow' if ok else 'DENY'} · {c.get('duration_ms') or 0:.1f}ms").style(
                            f"font-size:9px; color:{color};"
                        )

        refresh_strip()
        ui.timer(3.0, refresh_strip)


def main() -> None:
    ui.run(title="Calibrate", dark=True, port=int(os.environ.get("CALIBRATE_DASHBOARD_PORT", 8080)), reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
