"""The generation agent: takes a natural-language request, grounds itself in
the real schema via governed tool calls, and produces one real dbt model
SQL file. This is the Phase 1 wiring - a manual Anthropic tool-use loop
calling the exact same governed functions mcp_server/server.py exposes over
MCP, so every call the agent makes is policy-checked and audit-logged
identically either way.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

from agent.prompts import SYSTEM_PROMPT, TOOL_DEFINITIONS
from governance.policy import reset_session_counters
from mcp_server import tools as t

load_dotenv()

_MODEL = os.environ.get("CALIBRATE_MODEL", "claude-sonnet-5")
_MAX_TURNS = 8


def friendly_agent_error(exc: Exception) -> str:
    """Translate the exceptions generate_model() actually raises (mostly
    anthropic.APIError subclasses) into an actionable one-liner instead of
    a raw API error JSON blob. Shared by cli/demo.py and dashboard/app.py
    so the two surfaces give identical guidance.
    """
    message = str(exc)
    if "credit balance is too low" in message:
        return "the Anthropic account has no API credit left - add credits at console.anthropic.com/settings/billing"
    if "authentication" in message.lower() or "invalid x-api-key" in message.lower() or "401" in message:
        return "ANTHROPIC_API_KEY looks invalid - double-check it in .env"
    if "rate limit" in message.lower() or "429" in message:
        return "hit the Anthropic API rate limit - wait a moment and try again"
    return str(exc)


@dataclass
class GenerationResult:
    sql: str
    model_name: str
    turns: int
    tool_calls: list[dict[str, Any]]


def _dispatch_tool(name: str, tool_input: dict[str, Any]) -> Any:
    actor = "calibrate-agent"
    if name == "get_schema":
        return t.get_schema(tool_input["table"], actor=actor)
    if name == "get_historical_baseline":
        return t.get_historical_baseline(
            tool_input["table"], tool_input["metric"], tool_input.get("period", "baseline"), actor=actor
        )
    if name == "run_generated_model":
        return t.run_generated_model(tool_input["sql"], actor=actor)
    raise ValueError(f"Unknown tool '{name}'")


def _extract_sql(text: str) -> str | None:
    match = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _slugify(prompt: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", prompt.lower()).strip("_")
    return slug[:60] or "generated_model"


def generate_model(
    prompt: str,
    save_dir: str = "examples",
    on_step: Optional[Callable[[str], None]] = None,
) -> GenerationResult:
    """Run the full agent loop for one natural-language request and save the
    resulting dbt model to save_dir/<slug>.sql. Requires ANTHROPIC_API_KEY.

    on_step, if given, is called synchronously with one human-readable line
    after every real milestone (each LLM turn, each governed tool call with
    its actual measured duration, the final save) as it actually happens -
    not replayed afterward. dashboard/app.py uses this to stream the run
    live instead of showing a single "please wait" spinner.
    """
    import anthropic

    def _emit(line: str) -> None:
        if on_step is not None:
            on_step(line)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example) - "
            "get one at https://console.anthropic.com/settings/keys"
        )

    reset_session_counters()
    client = anthropic.Anthropic(api_key=api_key)

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    tool_calls: list[dict[str, Any]] = []
    final_text = ""

    _emit(f'agent.generate_model("{prompt}")')

    for turn in range(1, _MAX_TURNS + 1):
        _emit(f"  turn {turn}: calling {_MODEL}...")
        t0 = time.perf_counter()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        _emit(f"  turn {turn}: response in {(time.perf_counter() - t0) * 1000:.0f}ms")

        assistant_content = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b.text for b in response.content if b.type == "text"]
        final_text = "\n".join(text_blocks)

        if not tool_use_blocks:
            break

        tool_results = []
        for block in tool_use_blocks:
            t1 = time.perf_counter()
            try:
                result = _dispatch_tool(block.name, block.input)
                content = str(result)
                is_error = False
            except Exception as exc:  # noqa: BLE001 - surfaced back to the model as a tool error
                content = f"Error: {exc}"
                is_error = True
            duration_ms = (time.perf_counter() - t1) * 1000
            status = "ERROR" if is_error else "ok"
            arg_preview = ", ".join(f"{k}={v!r:.60}" for k, v in block.input.items() if k != "sql")
            _emit(f"    [governed] calibrate-agent -> {block.name}({arg_preview}) ... {status} ({duration_ms:.0f}ms)")
            tool_calls.append({"tool": block.name, "input": block.input, "error": is_error})
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})
    else:
        raise RuntimeError(f"Agent did not finish within {_MAX_TURNS} turns.")

    sql = _extract_sql(final_text)
    if not sql:
        raise RuntimeError(f"Agent finished without producing a ```sql block. Last response:\n{final_text}")

    model_name = _slugify(prompt)
    out_path = Path(save_dir) / f"{model_name}.sql"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sql + "\n", encoding="utf-8")
    _emit(f"  saved {out_path}")

    return GenerationResult(sql=sql, model_name=model_name, turns=turn, tool_calls=tool_calls)
