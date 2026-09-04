"""Calibrate's dashboard. Reads real state - validation_runs and audit_log
from data/calibrate_state.db - and can trigger real runs (generate,
validate, the full Phase 0-2 demo) live from the browser via NiceGUI.
Nothing on this page is sample/placeholder data once at least one demo run
has happened; before that, panels say so explicitly instead of faking it.

The console panel streams real progress: agent.core.generate_model() takes
an on_step callback and reports each LLM turn and each governed tool call
as it actually completes (with its real measured duration), threaded back
onto the UI event loop via an asyncio.Queue - this is genuine real-time
progress, not a replay.

Run with: python -m dashboard.app
"""

from __future__ import annotations

import asyncio
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

BG = "#07090d"
PANEL = "#11151d"
PANEL_2 = "#0c0f15"
PANEL_BORDER = "#232a3a"
TEXT = "#eef1f7"
MUTED = "#7d879b"
ACCENT = "#39e6a0"
ACCENT_DIM = "#1f6b4d"
DANGER = "#ff5d78"
DANGER_DIM = "#5c2430"
AMBER = "#f5b83d"
TEAL = "#31d8d1"
VIOLET = "#9b8cff"
BASELINE_COLOR = "#5c6785"

state: dict[str, Any] = {"selected_run_id": None}


def _backend_label() -> str:
    kind = os.environ.get("CALIBRATE_BACKEND", "duckdb")
    if kind == "snowflake":
        db = os.environ.get("SNOWFLAKE_DATABASE", "SNOWFLAKE_SAMPLE_DATA")
        schema = os.environ.get("SNOWFLAKE_SCHEMA", "TPCH_SF1")
        return f"SNOWFLAKE · {db}.{schema}"
    return "DUCKDB · tpch_sf1 (local TPC-H SF1)"


def _fmt_pct(x: Optional[float]) -> str:
    return f"{x:+.2%}" if x is not None else "—"


def _fmt_sigma(x: Optional[float]) -> str:
    return f"{x:+.2f}σ" if x is not None else "—"


# ---------------------------------------------------------------- styling --

def _inject_style() -> None:
    ui.add_head_html(f"""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
      html, body {{ background: {BG}; font-size: 15px; }}
      .q-page {{ background: {BG}; }}
      body, .q-field__native, .q-btn, textarea, input {{
        font-family: 'JetBrains Mono', 'Cascadia Code', ui-monospace, monospace;
      }}
      .cal-display {{ font-family: 'Space Grotesk', 'Segoe UI', sans-serif !important; }}
      .material-icons, .q-icon, i.material-icons {{ font-family: 'Material Icons' !important; }}

      .cal-bg {{
        position: fixed; inset: 0; z-index: 0; pointer-events: none;
        background-image:
          radial-gradient(ellipse 900px 500px at 15% -10%, rgba(57,230,160,0.09), transparent 60%),
          radial-gradient(ellipse 700px 500px at 100% 0%, rgba(155,140,255,0.07), transparent 55%),
          linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
        background-size: auto, auto, 42px 42px, 42px 42px;
      }}
      .cal-scanline {{
        position: fixed; left:0; right:0; height: 2px; z-index: 1; pointer-events: none;
        background: linear-gradient(90deg, transparent, rgba(57,230,160,0.55), transparent);
        animation: cal-scan 7s linear infinite; opacity: 0.7;
      }}
      @keyframes cal-scan {{ 0% {{ top: -2%; }} 100% {{ top: 102%; }} }}

      .cal-panel {{
        background: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 14px;
        position: relative;
      }}
      .cal-panel::before {{
        content:''; position:absolute; inset:0; border-radius:14px; padding:1px;
        background: linear-gradient(135deg, rgba(57,230,160,0.16), transparent 30%, transparent 70%, rgba(155,140,255,0.12));
        -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
        -webkit-mask-composite: xor; mask-composite: exclude; pointer-events:none;
      }}
      .cal-badge {{
        border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 700; letter-spacing: .04em;
      }}
      .cal-row-flash {{ animation: cal-flash 1.2s ease-out; }}
      @keyframes cal-flash {{ 0% {{ background: rgba(57,230,160,0.24); }} 100% {{ background: transparent; }} }}
      .cal-pulse {{
        display:inline-block; width:9px; height:9px; border-radius:50%; background: {ACCENT};
        animation: cal-pulse 2s infinite;
      }}
      @keyframes cal-pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba(57,230,160,0.55); }}
        70% {{ box-shadow: 0 0 0 9px rgba(57,230,160,0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(57,230,160,0); }}
      }}

      .cal-console {{
        background: {PANEL_2}; border: 1px solid {PANEL_BORDER}; border-radius: 12px;
        padding: 12px 14px; height: 230px; overflow-y: auto;
      }}
      .cal-console-line {{
        font-size: 12.5px; line-height: 1.7; white-space: pre-wrap; color: #9fb6c9;
        opacity: 0; animation: cal-line-in .3s ease forwards;
      }}
      .cal-console-line.ok {{ color: {ACCENT}; }}
      .cal-console-line.err {{ color: {DANGER}; }}
      .cal-console-line.head {{ color: {TEAL}; font-weight: 700; }}
      .cal-console-line.dim {{ color: {MUTED}; }}
      @keyframes cal-line-in {{ from {{ opacity:0; transform: translateY(3px);}} to {{ opacity:1; transform:none; }} }}
      .cal-cursor {{
        display:inline-block; width:7px; height:14px; background:{ACCENT}; margin-left:2px;
        animation: cal-blink 1s step-end infinite; vertical-align: -2px;
      }}
      @keyframes cal-blink {{ 50% {{ opacity: 0; }} }}

      .cal-flow {{ stroke-dasharray: 6 14; animation: cal-dash 1.1s linear infinite; }}
      @keyframes cal-dash {{ to {{ stroke-dashoffset: -20; }} }}
      .cal-node-glow {{ animation: cal-node-pulse 3s ease-in-out infinite; }}
      @keyframes cal-node-pulse {{
        0%, 100% {{ filter: drop-shadow(0 0 0px rgba(57,230,160,0)); }}
        50% {{ filter: drop-shadow(0 0 6px rgba(57,230,160,0.45)); }}
      }}

      .cal-particle {{
        position: fixed; border-radius: 50%; pointer-events: none; z-index: 0;
        animation-name: cal-float; animation-timing-function: ease-in-out; animation-iteration-count: infinite;
      }}
      @keyframes cal-float {{
        0%   {{ transform: translate(0,0); }}
        25%  {{ transform: translate(26px,-34px); }}
        50%  {{ transform: translate(-16px,-62px); }}
        75%  {{ transform: translate(-34px,-18px); }}
        100% {{ transform: translate(0,0); }}
      }}

      .cal-panel {{ transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease; }}
      .cal-panel:hover {{ transform: translateY(-2px); box-shadow: 0 10px 28px rgba(0,0,0,0.35); }}

      .cal-check-card {{
        background: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 12px; padding: 16px 18px;
        transition: transform .18s ease, box-shadow .18s ease;
      }}
      .cal-check-card:hover {{ transform: translateY(-2px); }}
      .cal-check-card.pass {{ border-color: rgba(57,230,160,0.35); }}
      .cal-check-card.fail {{ border-color: rgba(255,93,120,0.45); box-shadow: 0 0 22px rgba(255,93,120,0.08); }}

      ::-webkit-scrollbar {{ width: 9px; height: 9px; }}
      ::-webkit-scrollbar-thumb {{ background: {PANEL_BORDER}; border-radius: 5px; }}
    </style>
    <script>
      window.calCountUp = function(el, endValue, mode, duration) {{
        duration = duration || 900;
        if (!el) return;
        function fmt(v) {{
          if (mode === 'pct') return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
          if (mode === 'sigma') return (v >= 0 ? '+' : '') + v.toFixed(2) + 'σ';
          if (mode === 'int') return Math.round(v).toLocaleString();
          return v.toFixed(2);
        }}
        var startTime = null;
        function frame(ts) {{
          if (!startTime) startTime = ts;
          var t = Math.min(1, (ts - startTime) / duration);
          var eased = 1 - Math.pow(1 - t, 3);
          el.textContent = fmt(endValue * eased);
          if (t < 1) requestAnimationFrame(frame);
        }}
        requestAnimationFrame(frame);
      }}
    </script>
    """)


def badge(text: str, color: str, bg: str) -> ui.element:
    return ui.label(text).classes("cal-badge").style(f"color:{color}; background:{bg};")


def count_up(label: ui.label, end_value: float, mode: str, duration_ms: int = 900) -> None:
    ui.run_javascript(f"calCountUp(getElement({label.id}), {end_value!r}, {mode!r}, {duration_ms})")


# --------------------------------------------------------------- data i/o --

def load_runs(limit: int = 100) -> list[dict[str, Any]]:
    from validation import results_store

    return results_store.get_runs(limit)


def load_audit(limit: int = 40) -> list[dict[str, Any]]:
    from governance.audit_log import get_recent_calls

    return get_recent_calls(limit)


# ------------------------------------------------------------------- app --

def _particle_field(n: int = 16) -> None:
    import random

    random.seed(7)  # stable across reloads - motion without visual jitter on refresh
    colors = [ACCENT, TEAL, VIOLET]
    parts = ['<div class="cal-bg"></div><div class="cal-scanline"></div>']
    for i in range(n):
        size = random.randint(3, 7)
        left = random.uniform(2, 96)
        top = random.uniform(6, 92)
        color = random.choice(colors)
        dur = random.uniform(14, 26)
        delay = random.uniform(0, 10)
        opacity = random.uniform(0.25, 0.55)
        parts.append(
            f'<div class="cal-particle" style="left:{left}vw; top:{top}vh; width:{size}px; height:{size}px; '
            f'background:{color}; opacity:{opacity}; animation-duration:{dur}s; animation-delay:-{delay}s; '
            f'box-shadow:0 0 {size * 2}px {color};"></div>'
        )
    ui.html("".join(parts))


@ui.page("/")
def index() -> None:
    _inject_style()
    ui.dark_mode().enable()
    ui.colors(primary=ACCENT)
    _particle_field()

    with ui.column().classes("w-full h-screen no-wrap").style(f"background:transparent; padding:0; gap:0; position:relative; z-index:2;"):
        _header()
        with ui.row().classes("w-full flex-grow no-wrap").style("gap:0; min-height:0;"):
            with ui.column().style(
                f"width:320px; min-width:320px; height:100%; background:{PANEL}CC; backdrop-filter: blur(6px); "
                f"border-right:1px solid {PANEL_BORDER}; padding:16px; gap:16px; overflow-y:auto;"
            ):
                _sidebar()
            with ui.column().classes("flex-grow").style("height:100%; padding:20px; gap:18px; overflow-y:auto;"):
                _pipeline_diagram()
                _explainer_banner()
                _main_panel()
        _governance_strip()


def _explainer_banner() -> None:
    """A random visitor should be able to read this and understand the
    whole point of the project without reading a line of code. Everything
    it claims is backed by the check cards and charts directly below it.
    """
    with ui.row().classes("cal-panel w-full no-wrap items-start").style(
        f"padding:18px 22px; gap:18px; border-color:rgba(57,230,160,0.25);"
    ):
        ui.icon("psychology", size="28px").style(f"color:{ACCENT}; margin-top:2px; flex-shrink:0;")
        with ui.column().style("gap:6px;"):
            ui.label("Why this exists").classes("cal-display").style(
                f"font-size:14px; font-weight:700; color:{TEXT};"
            )
            ui.label(
                "An AI agent can write SQL that references only real columns, executes without error, and still "
                "quietly returns the wrong answer - a bad join, a missing GROUP BY, a metric that drifts. Schema "
                "grounding alone can't catch that; only checking the actual output can. Below, an agent generates "
                "a dbt model against a real TPC-H dataset, and three independent statistical checks decide whether "
                "to trust it - not whether it ran, whether its numbers hold up."
            ).style(f"font-size:12.5px; color:{MUTED}; line-height:1.6; max-width:980px;")
            with ui.row().classes("items-center").style("gap:18px; margin-top:2px;"):
                for icon, label in [
                    ("fingerprint", "Uniqueness — is each row's key actually unique?"),
                    ("balance", "Reconciliation — does the total match an independent control sum?"),
                    ("show_chart", "Drift — does recent output match the historical shape?"),
                ]:
                    with ui.row().classes("items-center").style("gap:6px;"):
                        ui.icon(icon, size="14px").style(f"color:{TEAL};")
                        ui.label(label).style(f"font-size:11.5px; color:{MUTED};")


def _header() -> None:
    with ui.row().classes("w-full items-center no-wrap").style(
        f"background:{PANEL}CC; backdrop-filter: blur(6px); border-bottom:1px solid {PANEL_BORDER}; padding:12px 22px; gap:20px;"
    ):
        ui.icon("hub", size="26px").style(f"color:{ACCENT};")
        with ui.column().style("gap:0;"):
            ui.label("Calibrate").classes("cal-display").style(f"font-size:20px; font-weight:800; color:{TEXT}; letter-spacing:.01em;")
            ui.label("Data Engine").style(f"font-size:11px; color:{MUTED}; letter-spacing:.05em;")
        ui.space()
        ui.label("TARGET").style(f"font-size:11px; color:{MUTED}; letter-spacing:.08em;")
        ui.label(_backend_label()).style(f"font-size:13px; color:{TEAL}; font-weight:600;")
        with ui.row().classes("items-center").style("gap:7px;"):
            ui.html('<span class="cal-pulse"></span>')
            ui.label("ACTIVE").style(f"font-size:12px; color:{ACCENT}; font-weight:700; letter-spacing:.05em;")
        clock = ui.label().style(f"font-size:13px; color:{MUTED}; min-width:170px;")
        ui.timer(1.0, lambda: clock.set_text(f"telemetry live · {datetime.datetime.now().strftime('%H:%M:%S')}"))


def _pipeline_diagram() -> None:
    """Always-visible, always-animating architecture strip - the system
    looks alive even before you click anything. Five real stages
    (source -> agent -> governed tools -> validation -> audit trail),
    connected by flowing dashes.
    """
    nodes = [
        ("SOURCE", "TPC-H SF1", ACCENT),
        ("AGENT", "claude tool-use", VIOLET),
        ("MCP TOOLS", "governed", TEAL),
        ("VALIDATION", "baseline_check", AMBER),
        ("GOVERNANCE", "audit_log.db", ACCENT),
    ]
    n = len(nodes)
    node_w, gap = 168, 26
    total_w = n * node_w + (n - 1) * gap
    node_h = 64
    cy = node_h / 2 + 8
    parts = [f'<svg viewBox="0 0 {total_w} {node_h + 16}" style="width:100%; height:84px;" preserveAspectRatio="xMidYMid meet">']
    parts.append(f'<defs><linearGradient id="calFlow" x1="0" y1="0" x2="1" y2="0">'
                  f'<stop offset="0%" stop-color="{TEAL}" stop-opacity="0"/>'
                  f'<stop offset="50%" stop-color="{TEAL}" stop-opacity="1"/>'
                  f'<stop offset="100%" stop-color="{TEAL}" stop-opacity="0"/></linearGradient></defs>')
    for i in range(n - 1):
        x1 = i * (node_w + gap) + node_w
        x2 = x1 + gap
        delay = i * 0.22
        parts.append(f'<line x1="{x1}" y1="{cy}" x2="{x2}" y2="{cy}" stroke="{PANEL_BORDER}" stroke-width="2"/>')
        parts.append(
            f'<line x1="{x1}" y1="{cy}" x2="{x2}" y2="{cy}" stroke="url(#calFlow)" stroke-width="2.5" '
            f'class="cal-flow" style="animation-delay:{delay}s"/>'
        )
    for i, (title, sub, color) in enumerate(nodes):
        x = i * (node_w + gap)
        parts.append(
            f'<g class="cal-node-glow" style="animation-delay:{i * 0.4}s">'
            f'<rect x="{x}" y="8" width="{node_w}" height="{node_h}" rx="11" '
            f'fill="{PANEL}" stroke="{color}" stroke-opacity="0.55" stroke-width="1.3"/>'
            f'<circle cx="{x + 18}" cy="{8 + node_h / 2}" r="4" fill="{color}"/>'
            f'<text x="{x + 32}" y="{8 + node_h / 2 - 3}" fill="{TEXT}" font-size="12.5" '
            f'font-weight="700" font-family="Space Grotesk, sans-serif">{title}</text>'
            f'<text x="{x + 32}" y="{8 + node_h / 2 + 15}" fill="{MUTED}" font-size="10.5" '
            f'font-family="JetBrains Mono, monospace">{sub}</text>'
            f'</g>'
        )
    parts.append("</svg>")
    with ui.column().classes("cal-panel w-full").style("padding:10px 18px;"):
        ui.html("".join(parts))


_MANUAL_OPTION = "__manual__"


def _model_select_options() -> dict[Any, str]:
    """Real catalog data, deduplicated to one entry per model (newest run),
    plus a trailing 'add manually' entry - this is what backs the dropdown,
    not a placeholder list.
    """
    runs = load_runs()
    seen: dict[str, dict[str, Any]] = {}
    for r in runs:
        seen.setdefault(r["model_name"], r)  # newest first already
    options: dict[Any, str] = {}
    for name, r in seen.items():
        mark = "✓" if r["verdict"] == "VERIFIED" else "✕"
        options[r["id"]] = f"{mark}  {name}"
    options[_MANUAL_OPTION] = "+  Add a model manually..."
    return options


def _sidebar() -> None:
    ui.label("PIPELINE CATALOG").classes("cal-display").style(f"font-size:12px; font-weight:700; color:{MUTED}; letter-spacing:.1em;")

    model_select = ui.select(
        options=_model_select_options(), label="Select or search a model", with_input=True,
    ).props("dense outlined dark").classes("w-full").style(f"--q-primary:{ACCENT}; font-size:13px;")

    with ui.column().style("max-height:260px; overflow-y:auto; width:100%; gap:0;"):
        _catalog_list()

    ui.separator().style(f"background:{PANEL_BORDER};")
    ui.label("NEW GENERATION").classes("cal-display").style(f"font-size:12px; font-weight:700; color:{MUTED}; letter-spacing:.1em;")
    prompt = ui.textarea(placeholder="e.g. generate a dbt model for monthly revenue by region").props(
        "dense outlined dark rows=3"
    ).classes("w-full").style("font-size:13px;")

    def _on_model_select(e: Any) -> None:
        value = e.value
        if value == _MANUAL_OPTION or value is None:
            model_select.set_value(None)
            # getElement() can return either the raw DOM node or a Vue
            # component instance (with the DOM node under .$el) depending
            # on the element type - handle both rather than assume one.
            ui.run_javascript(
                f"let el = getElement({prompt.id}); "
                f"if (el && el.$el) el = el.$el; "
                f"if (el && typeof el.scrollIntoView === 'function') "
                f"el.scrollIntoView({{behavior:'smooth', block:'center'}}); "
                f"const inner = el && el.querySelector ? el.querySelector('textarea') : null; "
                f"if (inner) inner.focus();"
            )
            return
        _select_run(value)

    model_select.on_value_change(_on_model_select)

    ui.label("LIVE RUN CONSOLE").classes("cal-display").style(f"font-size:11px; font-weight:700; color:{MUTED}; letter-spacing:.1em; margin-top:4px;")
    console = ui.column().classes("cal-console w-full")
    with console:
        ui.label("$ waiting for a run...").classes("cal-console-line dim")

    async def _print(text: str, cls: str = "cal-console-line") -> None:
        with console:
            ui.label(text).classes(cls)
        await ui.run_javascript(
            f"const p = getElement({console.id}); if (p) p.scrollTop = p.scrollHeight;", timeout=2.0
        )

    def _report_lines(report: dict[str, Any]) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        dup = report.get("duplicate_group_rows", 0)
        lines.append((f"  [check] grouping-key uniqueness ... {'FLAGGED' if dup else 'ok'} ({dup} duplicate row(s))",
                       "cal-console-line err" if dup else "cal-console-line ok"))
        if report.get("reference_total") is not None:
            delta = report.get("reconciliation_delta_pct") or 0.0
            flagged = delta > 0.005
            lines.append((
                f"  [check] reconciliation ... {'FLAGGED' if flagged else 'ok'} "
                f"(model {report['model_total']:,.0f} vs ref {report['reference_total']:,.0f}, Δ {delta:+.2%})",
                "cal-console-line err" if flagged else "cal-console-line ok",
            ))
        sigma = report.get("distribution_drift_sigma")
        if sigma is not None:
            flagged = abs(sigma) > 3.0
            lines.append((f"  [check] historical drift ... {'FLAGGED' if flagged else 'ok'} ({sigma:+.2f}σ)",
                           "cal-console-line err" if flagged else "cal-console-line ok"))
        lines.append((f"VERDICT: {report['verdict']}", "cal-console-line head"))
        return lines

    async def do_generate() -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            console.clear()
            await _print("ERROR: ANTHROPIC_API_KEY not set - see .env.example", "cal-console-line err")
            return
        if not (prompt.value or "").strip():
            console.clear()
            await _print(
                'ERROR: type a prompt first, e.g. "generate a dbt model for monthly revenue by region"',
                "cal-console-line err",
            )
            return
        console.clear()
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_step(line: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, line)

        async def consume() -> None:
            while True:
                line = await queue.get()
                if line is None:
                    return
                cls = "cal-console-line err" if "ERROR" in line else "cal-console-line"
                await _print(line, cls)

        consumer_task = asyncio.ensure_future(consume())
        try:
            from agent.core import generate_model

            result = await run.io_bound(generate_model, prompt.value, "examples", on_step)
            await queue.put(None)
            await consumer_task
            await _print(f"$ calibrate validate examples/{result.model_name}.sql", "cal-console-line head")

            from validation.drift_report import run_and_report

            report = await run.io_bound(
                run_and_report, result.model_name, result.sql, "order_month", "total_revenue", ["region"],
                f"examples/{result.model_name}.sql",
            )
            for text, cls in _report_lines(report):
                await _print(text, cls)
                await asyncio.sleep(0.12)
        except Exception as exc:  # noqa: BLE001 - surfaced to the console, not swallowed
            from agent.core import friendly_agent_error

            await queue.put(None)
            if not consumer_task.done():
                await consumer_task
            await _print(f"ERROR: {friendly_agent_error(exc)}", "cal-console-line err")
        _catalog_list.refresh("")
        _main_panel.refresh()

    generate_btn = ui.button("Generate + Validate", on_click=do_generate).props("unelevated").classes("w-full").style(
        f"background:{ACCENT}; color:#04120c; font-weight:700; font-size:13px;"
    )
    generate_btn.bind_enabled_from(prompt, "value", backward=lambda v: bool(v and v.strip()))

    ui.separator().style(f"background:{PANEL_BORDER};")

    async def do_full_demo() -> None:
        console.clear()
        await _print("$ calibrate run-demo", "cal-console-line head")
        from mcp_server.tools import get_schema, get_historical_baseline
        from validation.drift_report import run_and_report

        t0 = time.perf_counter()
        schema = await run.io_bound(get_schema, "orders", actor="demo-cli")
        await _print(
            f"  [governed] demo-cli -> get_schema(table='orders') ... ok "
            f"({(time.perf_counter() - t0) * 1000:.0f}ms) — {schema['row_count']:,} rows",
            "cal-console-line ok",
        )

        t0 = time.perf_counter()
        baseline = await run.io_bound(get_historical_baseline, "orders", "o_totalprice", "baseline", actor="demo-cli")
        await _print(
            f"  [governed] demo-cli -> get_historical_baseline(...) ... ok "
            f"({(time.perf_counter() - t0) * 1000:.0f}ms) — mean={baseline['mean']:.2f}",
            "cal-console-line ok",
        )

        from examples.registry import DEMO_MODELS

        for m in DEMO_MODELS:
            sql = Path(m["path"]).read_text(encoding="utf-8")
            await _print(f"$ calibrate validate {m['path']}", "cal-console-line head")
            report = await run.io_bound(
                run_and_report, m["name"], sql, m["period_col"], m["metric_col"], m["dimension_cols"], m["path"],
            )
            for text, cls in _report_lines(report):
                await _print(text, cls)
                await asyncio.sleep(0.08)
            _catalog_list.refresh("")
        await _print(f"done - {len(DEMO_MODELS)} models validated, see catalog", "cal-console-line dim")
        _catalog_list.refresh("")
        _main_panel.refresh()

    ui.button(f"Run Full Demo (5 models)", on_click=do_full_demo).props("outline").classes("w-full").style(
        f"color:{TEAL}; border-color:{TEAL}; font-size:13px;"
    )


@ui.refreshable
def _catalog_list(filter_text: str = "") -> None:
    runs = load_runs()
    seen_models: dict[str, dict[str, Any]] = {}
    for r in runs:
        seen_models.setdefault(r["model_name"], r)  # newest first already
    filter_text = (filter_text or "").lower()

    if not seen_models:
        ui.label("No runs yet - click 'Run Phase 0-2 Demo' below.").style(f"font-size:13px; color:{MUTED}; line-height:1.5;")
        return

    with ui.column().classes("w-full").style("gap:8px;"):
        for model_name, r in seen_models.items():
            if filter_text and filter_text not in model_name.lower():
                continue
            is_selected = state["selected_run_id"] == r["id"]
            border = ACCENT if is_selected else PANEL_BORDER
            glow = f"box-shadow: 0 0 14px rgba(57,230,160,0.18);" if is_selected else ""
            with ui.row().classes("w-full items-center cursor-pointer no-wrap").style(
                f"border:1px solid {border}; border-radius:10px; padding:10px 12px; gap:10px; {glow} transition: all .15s ease;"
            ).on("click", lambda r=r: _select_run(r["id"])):
                with ui.column().style("gap:2px; min-width:0; width:0; flex-grow:1; overflow:hidden;"):
                    ui.label(f"{model_name}.sql").style(
                        f"font-size:13px; color:{TEXT}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; width:100%;"
                    )
                    ui.label(r["sql_path"] or "").style(
                        f"font-size:11px; color:{MUTED}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; width:100%;"
                    )
                with ui.row().style("flex-shrink:0;"):
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
        with ui.column().classes("w-full items-center justify-center").style("height:50vh; gap:10px;"):
            ui.icon("insights", size="56px").style(f"color:{PANEL_BORDER};")
            ui.label("No validation runs yet").classes("cal-display").style(f"font-size:16px; color:{MUTED};")
            ui.label("Run the Phase 0-2 demo from the sidebar to see real numbers here.").style(
                f"font-size:13px; color:{MUTED};"
            )
        return

    run = results_store.get_run(run_id)
    if run is None:
        return
    report = run["report"]

    with ui.row().classes("w-full items-center"):
        ui.label(f"DASHBOARDS  ›  {run['model_name']}").style(f"font-size:12px; color:{MUTED}; letter-spacing:.05em;")
        ui.space()
        verdict = run["verdict"]
        overall_color = ACCENT if verdict == "VERIFIED" else DANGER
        badge(
            f"{'✓ TRUSTED' if verdict == 'VERIFIED' else '✕ DO NOT SHIP'} — {verdict}",
            "#04120c" if verdict == "VERIFIED" else "#2a0a10", overall_color,
        )

    with ui.row().classes("w-full no-wrap").style("gap:16px;"):
        _stat_tile("ROW COUNT DELTA", report.get("row_count_delta_pct"), "pct", 0.25, "rows/period, recent vs historical")
        _stat_tile("NULL RATE VARIANCE", report.get("null_rate_variance"), "pct", 0.02, "missing values, recent vs historical")
        _stat_tile("DISTRIBUTION DRIFT", report.get("distribution_drift_sigma"), "sigma", 3.0, "z-score vs baseline mean")
        with ui.column().classes("cal-panel").style("padding:18px; gap:4px; min-width:220px;"):
            ui.label("RECONCILIATION").style(f"font-size:12px; color:{MUTED}; letter-spacing:.06em; font-weight:700;")
            delta = report.get("reconciliation_delta_pct")
            color = DANGER if (delta or 0) > 0.005 else ACCENT
            value_label = ui.label("0.00%" if delta is not None else "skipped").style(
                f"font-size:27px; font-weight:800; color:{color};"
            )
            if delta is not None:
                count_up(value_label, delta * 100, "pct")
            ui.label("model total vs independent control sum").style(f"font-size:11px; color:{MUTED};")

    ui.label("THE THREE CHECKS, IN PLAIN ENGLISH").classes("cal-display").style(
        f"font-size:11px; font-weight:700; color:{MUTED}; letter-spacing:.1em; margin-top:4px;"
    )
    with ui.row().classes("w-full no-wrap").style("gap:16px;"):
        dup = report.get("duplicate_group_rows", 0) or 0
        total_rows = report.get("total_row_count", 0) or 0
        key_desc = ", ".join(report.get("dimension_cols") or ["region"]) + f", {report.get('period_col', 'month')}"
        _check_card(
            "fingerprint", "UNIQUENESS", dup == 0,
            f"{total_rows - dup:,} of {total_rows:,} rows have a one-of-a-kind ({key_desc}) key." if dup == 0
            else f"{dup:,} row(s) share a ({key_desc}) key that should be unique - the GROUP BY is likely missing a column.",
        )
        ref_total = report.get("reference_total")
        model_total = report.get("model_total")
        recon_ok = delta is None or delta <= 0.005
        _check_card(
            "balance", "RECONCILIATION", recon_ok,
            (f"Model total ${model_total:,.0f} matches the independent control sum exactly." if recon_ok and delta == 0
             else f"Model total ${model_total:,.0f} is within {(delta or 0):.2%} of the ${ref_total:,.0f} control sum." if recon_ok
             else f"Model total ${model_total:,.0f} is {(delta or 0):+.1%} off the ${ref_total:,.0f} control sum - a join is likely fanning out rows.")
            if ref_total is not None else "Reconciliation not applicable for this model.",
        )
        sigma = report.get("distribution_drift_sigma")
        drift_ok = sigma is None or abs(sigma) <= 3.0
        _check_card(
            "show_chart", "HISTORICAL DRIFT", drift_ok,
            f"Recent-period average is {sigma:+.2f}σ from the historical baseline - within the ±3.0σ normal range." if sigma is not None and drift_ok
            else f"Recent-period average is {sigma:+.2f}σ from the historical baseline - outside the ±3.0σ normal range." if sigma is not None
            else "Not enough periods to compute drift.",
        )

    with ui.row().classes("w-full no-wrap").style("gap:16px; align-items:stretch;"):
        with ui.column().classes("cal-panel").style("padding:20px; gap:10px; flex:1;"):
            ui.label("Reconciliation: model total vs. independent control").classes("cal-display").style(
                f"font-size:13px; font-weight:700; color:{TEXT};"
            )
            _reconciliation_bar_chart(report.get("model_total"), report.get("reference_total"))
        with ui.column().classes("cal-panel").style("padding:20px; gap:10px; flex:1;"):
            ui.label("Governance: every governed call, ever").classes("cal-display").style(
                f"font-size:13px; font-weight:700; color:{TEXT};"
            )
            _governance_donut()

    with ui.column().classes("cal-panel w-full").style("padding:20px; gap:12px;"):
        with ui.row().classes("items-center w-full"):
            ui.label("Generated Model Output vs Historical Baseline").classes("cal-display").style(
                f"font-size:14px; font-weight:700; color:{TEXT};"
            )
            ui.space()
            badge(verdict, "#04120c" if verdict == "VERIFIED" else "#2a0a10", overall_color)
        _drift_chart(report)

    if run["flags"]:
        ui.label("FAILURE DETAIL").classes("cal-display").style(
            f"font-size:11px; font-weight:700; color:{MUTED}; letter-spacing:.1em;"
        )
        with ui.column().classes("w-full").style("gap:8px;"):
            for flag in run["flags"]:
                with ui.row().classes("w-full no-wrap items-start").style(
                    f"border:1px solid {DANGER}; background:{DANGER_DIM}33; border-radius:10px; padding:14px 16px; gap:10px;"
                ):
                    ui.icon("warning", size="18px").style(f"color:{DANGER}; margin-top:2px;")
                    ui.label(flag).style(f"font-size:13px; color:{TEXT}; white-space:pre-wrap; line-height:1.5;")

    with ui.expansion("Resolved SQL", icon="code").classes("w-full cal-panel").style(f"color:{TEXT}; font-size:13px;"):
        ui.code(report.get("resolved_sql", ""), language="sql").classes("w-full")


def _stat_tile(label: str, raw_value: Optional[float], mode: str, threshold: float, subtitle: str = "") -> None:
    flagged = raw_value is not None and abs(raw_value) > threshold
    color = DANGER if flagged else ACCENT
    with ui.column().classes("cal-panel").style("padding:18px; gap:4px; min-width:220px;"):
        ui.label(label).style(f"font-size:12px; color:{MUTED}; letter-spacing:.06em; font-weight:700;")
        placeholder = "—" if raw_value is None else ("0.00%" if mode == "pct" else "0.00σ")
        value_label = ui.label(placeholder).style(f"font-size:27px; font-weight:800; color:{color};")
        if raw_value is not None:
            count_up(value_label, raw_value * 100 if mode == "pct" else raw_value, mode)
        if flagged:
            badge("FLAGGED", "#2a0a10", DANGER)
        elif subtitle:
            ui.label(subtitle).style(f"font-size:11px; color:{MUTED};")
        else:
            ui.label("within tolerance").style(f"font-size:12px; color:{MUTED};")


def _check_card(icon: str, title: str, passed: bool, description: str) -> None:
    color = ACCENT if passed else DANGER
    with ui.column().classes(f"cal-check-card {'pass' if passed else 'fail'}").style("flex:1; gap:8px;"):
        with ui.row().classes("items-center w-full").style("gap:10px;"):
            ui.icon(icon, size="18px").style(f"color:{color};")
            ui.label(title).classes("cal-display").style(f"font-size:12.5px; font-weight:700; color:{TEXT}; letter-spacing:.03em;")
            ui.space()
            ui.icon("check_circle" if passed else "cancel", size="18px").style(f"color:{color};")
        ui.label(description).style(f"font-size:12px; color:{MUTED}; line-height:1.55;")


def _reconciliation_bar_chart(model_total: Optional[float], reference_total: Optional[float]) -> None:
    if model_total is None or reference_total is None:
        ui.label("Reconciliation not computed for this model.").style(f"font-size:12px; color:{MUTED};")
        return
    max_abs = max(abs(model_total), abs(reference_total), 1)
    if max_abs >= 1e9:
        div, unit = 1e9, "$B"
    elif max_abs >= 1e6:
        div, unit = 1e6, "$M"
    else:
        div, unit = 1e3, "$K"
    mismatch = reference_total != 0 and abs(model_total - reference_total) / abs(reference_total) > 0.005
    model_color = DANGER if mismatch else ACCENT
    options = {
        "backgroundColor": "transparent",
        "textStyle": {"color": TEXT, "fontFamily": "JetBrains Mono, monospace"},
        "grid": {"left": 110, "right": 60, "top": 10, "bottom": 10},
        "xAxis": {"type": "value", "axisLine": {"show": False}, "splitLine": {"lineStyle": {"color": PANEL_BORDER}},
                  "axisLabel": {"color": MUTED, "fontSize": 11}},
        "yAxis": {"type": "category", "data": ["Model output", "Independent control"],
                  "axisLine": {"lineStyle": {"color": PANEL_BORDER}}, "axisLabel": {"color": MUTED, "fontSize": 12}},
        # Two single-bar series rather than one series with two data points -
        # ui.echart passes options straight through as JSON (no JS callback
        # support, see _drift_chart's y-axis formatter fix above for the
        # same lesson learned the first time), so a real per-bar stagger
        # needs two series with their own plain numeric animationDelay
        # instead of an animationDelay *function* keyed on data index.
        "series": [
            {
                "name": "Model output", "type": "bar", "barWidth": 28, "barGap": "-100%",
                "data": [round(model_total / div, 3), None],
                "itemStyle": {"color": model_color, "borderRadius": [0, 6, 6, 0], "shadowBlur": 10, "shadowColor": model_color},
                "label": {"show": True, "position": "right", "color": MUTED, "fontSize": 11, "formatter": "{c} " + unit},
                "emphasis": {"itemStyle": {"shadowBlur": 24}},
                "animationDuration": 1300, "animationEasing": "elasticOut", "animationDelay": 0,
            },
            {
                "name": "Independent control", "type": "bar", "barWidth": 28,
                "data": [None, round(reference_total / div, 3)],
                "itemStyle": {"color": TEAL, "borderRadius": [0, 6, 6, 0], "shadowBlur": 10, "shadowColor": TEAL},
                "label": {"show": True, "position": "right", "color": MUTED, "fontSize": 11, "formatter": "{c} " + unit},
                "emphasis": {"itemStyle": {"shadowBlur": 24}},
                "animationDuration": 1300, "animationEasing": "elasticOut", "animationDelay": 220,
            },
        ],
    }
    chart = ui.echart(options).classes("w-full").style("height: 150px;")

    # Keeps the chart visibly alive at rest, not just animating once on
    # first paint - a slow highlight sweep alternating between the two bars.
    # Self-limiting and defensive: _main_panel is @ui.refreshable, so
    # switching between catalog models tears this chart out of the DOM
    # without NiceGUI auto-cancelling the timer that references it. Bound
    # the sweep to a handful of cycles and stop immediately on any error
    # (a removed element) rather than leaking timers on stale charts.
    pulse_state = {"cycles": 0}

    async def _pulse() -> None:
        if pulse_state["cycles"] >= 6:
            pulse_timer.cancel()
            return
        pulse_state["cycles"] += 1
        try:
            for series_idx in (0, 1):
                await chart.run_chart_method("dispatchAction", {"type": "downplay", "seriesIndex": [0, 1]})
                await chart.run_chart_method("dispatchAction", {"type": "highlight", "seriesIndex": series_idx})
                await asyncio.sleep(1.4)
            await chart.run_chart_method("dispatchAction", {"type": "downplay", "seriesIndex": [0, 1]})
        except Exception:
            pulse_timer.cancel()

    pulse_timer = ui.timer(3.2, _pulse)

    ui.timer(3.2, _pulse)


def _governance_donut() -> None:
    from governance.audit_log import get_call_counts_by_tool

    rows = get_call_counts_by_tool()
    if not rows:
        ui.label("No governed calls logged yet.").style(f"font-size:12px; color:{MUTED};")
        return
    palette = [ACCENT, TEAL, VIOLET, AMBER, DANGER]
    data = [{"name": r["tool"], "value": r["calls"]} for r in rows]
    total_denied = sum(r["denied"] for r in rows)
    total_calls = sum(r["calls"] for r in rows)
    # PIE_CENTER_X is where the pie's center sits, as a fraction of the
    # chart's full width. An overlay box spanning [0, 2*PIE_CENTER_X] has
    # its own natural center exactly on top of the pie's center - plain
    # CSS flex-centering inside that box, instead of guessing ECharts
    # `graphic` pixel/percent offsets against a layout that also has a
    # legend eating into the canvas (which is what kept misaligning it).
    PIE_CENTER_X = 0.34
    options = {
        "backgroundColor": "transparent",
        "textStyle": {"color": TEXT, "fontFamily": "JetBrains Mono, monospace"},
        "tooltip": {"trigger": "item", "backgroundColor": PANEL, "borderColor": PANEL_BORDER, "textStyle": {"color": TEXT}},
        "legend": {"orient": "vertical", "right": 4, "top": "center", "textStyle": {"color": MUTED, "fontSize": 11}, "itemWidth": 12, "itemHeight": 12},
        "color": palette,
        "series": [{
            "type": "pie", "radius": ["46%", "72%"], "center": [f"{PIE_CENTER_X * 100:.0f}%", "50%"],
            "avoidLabelOverlap": True,
            "itemStyle": {"borderColor": PANEL, "borderWidth": 2},
            "label": {"show": False},
            "emphasis": {"scale": True, "scaleSize": 6},
            "data": data,
            "animationType": "expansion", "animationDuration": 1100,
        }],
    }
    with ui.element("div").style("position:relative; width:100%; height:150px;"):
        ui.echart(options).classes("w-full").style("height: 150px;")
        with ui.column().style(
            f"position:absolute; inset:0 0 0 0; width:{PIE_CENTER_X * 200:.0f}%; height:150px; "
            f"align-items:center; justify-content:center; gap:0; pointer-events:none;"
        ):
            ui.label(f"{total_calls}").style(f"font-size:22px; font-weight:800; color:{TEXT}; line-height:1.1;")
            ui.label("calls").style(f"font-size:10px; color:{MUTED};")
    ui.label(f"{total_denied} of {total_calls} calls were denied by policy.").style(f"font-size:11px; color:{MUTED};")


def _drift_chart(report: dict[str, Any]) -> None:
    from validation.config import CUTOFF_MONTH, Z_SCORE_THRESHOLD

    resolved_sql = report.get("resolved_sql", "")

    # Re-run the resolved SQL live so the chart reflects the exact rows the
    # verdict was computed from - no separately-maintained chart dataset.
    try:
        from db.connection import get_backend

        backend = get_backend()
        cols, rows = backend.execute(resolved_sql)
        idx = {c: i for i, c in enumerate(cols)}
    except Exception:
        cols, rows, idx = [], [], {}

    period_col = report.get("period_col", "order_month")
    metric_col = report.get("metric_col", "total_revenue")
    series: dict[str, float] = {}
    if period_col in idx and metric_col in idx:
        for r in rows:
            p = r[idx[period_col]]
            key = f"{p.year:04d}-{p.month:02d}" if hasattr(p, "year") else str(p)[:7]
            series[key] = series.get(key, 0.0) + float(r[idx[metric_col]] or 0)

    periods = sorted(series.keys())
    raw_values = [series[p] for p in periods]

    # Pick a display unit and rescale in Python - ui.echart passes options
    # straight through as JSON, so a JS-formatter *string* here would just
    # render as literal text, not execute. Rescaling the data itself avoids
    # needing a callback at all, and keeps axis labels short and legible at
    # TPC-H's ~$100M-15B monthly-revenue scale.
    max_abs = max((abs(v) for v in raw_values), default=0)
    if max_abs >= 1e9:
        unit_div, unit_label = 1e9, "$B"
    elif max_abs >= 1e6:
        unit_div, unit_label = 1e6, "$M"
    elif max_abs >= 1e3:
        unit_div, unit_label = 1e3, "$K"
    else:
        unit_div, unit_label = 1.0, "$"
    values = [v / unit_div for v in raw_values]

    baseline_vals = [v if p < CUTOFF_MONTH else None for p, v in zip(periods, values)]
    recent_vals = [v if p >= CUTOFF_MONTH else None for p, v in zip(periods, values)]
    first_recent = next((p for p in periods if p >= CUTOFF_MONTH), None)

    flagged_recent = bool(report.get("flags"))
    live_color = DANGER if flagged_recent else TEAL

    generated_series: dict[str, Any] = {
        "name": "Generated model output",
        "type": "line", "data": recent_vals, "smooth": True, "symbol": "circle", "symbolSize": 6,
        "lineStyle": {"color": live_color, "width": 2.5, "shadowBlur": 12, "shadowColor": live_color},
        "areaStyle": {"color": live_color, "opacity": 0.16},
        "connectNulls": False,
        "animationDuration": 1600,
        "animationEasing": "elasticOut",
    }
    if flagged_recent and first_recent is not None and periods:
        generated_series["markArea"] = {
            "silent": True,
            "itemStyle": {"color": DANGER, "opacity": 0.10},
            "label": {"show": True, "position": "insideTopLeft", "color": DANGER, "fontSize": 11, "fontWeight": 700},
            "data": [[
                {"xAxis": first_recent, "name": f"  ANOMALY DRIFT (>{Z_SCORE_THRESHOLD:.1f}σ)"},
                {"xAxis": periods[-1]},
            ]],
        }

    extra_series = []
    if flagged_recent and periods and recent_vals[-1] is not None:
        extra_series.append({
            "name": "Anomaly",
            "type": "effectScatter",
            "data": [[periods[-1], recent_vals[-1]]],
            "symbolSize": 12,
            "rippleEffect": {"brushType": "stroke", "scale": 4, "period": 2.2},
            "itemStyle": {"color": DANGER},
            "z": 10,
        })

    options = {
        "backgroundColor": "transparent",
        "textStyle": {"color": TEXT, "fontFamily": "JetBrains Mono, monospace"},
        "grid": {"left": 74, "right": 32, "top": 48, "bottom": 44},
        "xAxis": {
            "type": "category", "data": periods, "boundaryGap": False,
            "axisLine": {"lineStyle": {"color": PANEL_BORDER}},
            "axisLabel": {"color": MUTED, "fontSize": 11},
        },
        "yAxis": {
            "type": "value",
            "name": f"revenue ({unit_label})",
            "nameLocation": "end",
            "nameGap": 14,
            "nameTextStyle": {"color": MUTED, "fontSize": 11, "align": "left"},
            "axisLine": {"show": False},
            "splitLine": {"lineStyle": {"color": PANEL_BORDER}},
            "axisLabel": {"color": MUTED, "fontSize": 11},
        },
        "tooltip": {"trigger": "axis", "backgroundColor": PANEL, "borderColor": PANEL_BORDER, "textStyle": {"color": TEXT}},
        "series": [
            {
                "name": "Historical baseline",
                "type": "line", "data": baseline_vals, "smooth": True, "symbol": "none",
                "lineStyle": {"color": BASELINE_COLOR, "width": 2},
                "areaStyle": {"color": BASELINE_COLOR, "opacity": 0.10},
                "connectNulls": False,
                "animationDuration": 1000,
            },
            generated_series,
            *extra_series,
        ],
        "legend": {
            "data": ["Historical baseline", "Generated model output"],
            "textStyle": {"color": MUTED, "fontSize": 12}, "top": 6, "right": 8,
            "itemWidth": 16, "itemGap": 18,
        },
    }
    ui.echart(options).classes("w-full").style("height: 360px;")


def _governance_strip() -> None:
    with ui.column().style(
        f"background:{PANEL}CC; backdrop-filter: blur(6px); border-top:1px solid {PANEL_BORDER}; "
        f"padding:12px 20px; gap:8px; max-height:190px;"
    ):
        with ui.row().classes("items-center").style("gap:8px;"):
            ui.icon("shield", size="16px").style(f"color:{TEAL};")
            ui.label("GOVERNANCE — live audit trail").classes("cal-display").style(
                f"font-size:12px; font-weight:700; color:{MUTED}; letter-spacing:.06em;"
            )
        rows_container = ui.row().classes("w-full no-wrap").style("gap:10px; overflow-x:auto; padding-bottom:6px;")
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
                        f"border:1px solid {PANEL_BORDER}; border-left:4px solid {color}; border-radius:8px; "
                        f"padding:8px 14px; min-width:190px; gap:3px; flex-shrink:0;"
                    ):
                        ui.label(f"{c['actor']} → {c['tool']}").style(
                            f"font-size:13px; color:{TEXT}; font-weight:600; white-space:nowrap;"
                        )
                        ui.label(f"{'ALLOW' if ok else 'DENY'} · {c.get('duration_ms') or 0:.1f}ms").style(
                            f"font-size:12px; color:{color}; font-weight:700;"
                        )

        refresh_strip()
        ui.timer(3.0, refresh_strip)


def main() -> None:
    ui.run(title="Calibrate", dark=True, port=int(os.environ.get("CALIBRATE_DASHBOARD_PORT", 8080)), reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
