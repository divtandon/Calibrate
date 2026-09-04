"""The generation agent: takes a natural-language request, grounds itself in
the real schema via governed tool calls, and produces one real dbt model
SQL file. This is the Phase 1 wiring - a manual tool-use loop calling the
exact same governed functions mcp_server/server.py exposes over MCP, so
every call the agent makes is policy-checked and audit-logged identically
either way.

Two providers are supported, selected by CALIBRATE_LLM_PROVIDER
("anthropic", the default, or "gemini"). Both loops share _dispatch_tool,
_extract_sql, and _slugify - only the wire format for the tool-use
conversation differs.
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

_ANTHROPIC_MODEL = os.environ.get("CALIBRATE_MODEL", "claude-sonnet-5")
_GEMINI_MODEL = os.environ.get("CALIBRATE_GEMINI_MODEL", "gemini-flash-latest")
_MAX_TURNS = 8
_GEMINI_MAX_RETRIES = 3


def _call_gemini_with_retry(client: Any, model: str, contents: Any, config: Any, emit: Callable[[str], None]) -> Any:
    """Gemini's free-tier flash models return a transient 503 'high demand'
    error often enough in practice that it needs handling here, not just a
    hard failure - confirmed live: two straight 503s followed immediately
    by a clean success on the third identical request, no code change in
    between. Anthropic's SDK already retries transient errors internally;
    google-genai's client does not, so this loop does it explicitly.
    """
    from google.genai import errors

    last_exc: Exception | None = None
    for attempt in range(1, _GEMINI_MAX_RETRIES + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except errors.ServerError as exc:
            last_exc = exc
            if attempt < _GEMINI_MAX_RETRIES:
                wait = 2 * attempt
                emit(f"    ({model} reported high demand - retrying in {wait}s, attempt {attempt}/{_GEMINI_MAX_RETRIES})")
                time.sleep(wait)
    raise last_exc


def friendly_agent_error(exc: Exception) -> str:
    """Translate the exceptions generate_model() actually raises (Anthropic
    or Gemini API errors) into an actionable one-liner instead of a raw API
    error JSON blob. Shared by cli/demo.py and dashboard/app.py so both
    surfaces give identical guidance.
    """
    message = str(exc)
    lower = message.lower()
    if "credit balance is too low" in message:
        return "the Anthropic account has no API credit left - add credits at console.anthropic.com/settings/billing"
    if "api key not valid" in lower or "api_key_invalid" in lower:
        return "GEMINI_API_KEY looks invalid - double-check it in .env (get one at aistudio.google.com/apikey)"
    if "resource_exhausted" in lower or ("quota" in lower and "gemini" in lower):
        return "hit the Gemini free-tier quota - wait a minute and try again, or check aistudio.google.com/apikey for your limits"
    if "authentication" in lower or "invalid x-api-key" in lower or "401" in message:
        return "ANTHROPIC_API_KEY looks invalid - double-check it in .env"
    if "rate limit" in lower or "429" in message:
        return "hit the API rate limit - wait a moment and try again"
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


def _save_sql(prompt: str, sql: str, save_dir: str, emit: Callable[[str], None]) -> str:
    model_name = _slugify(prompt)
    out_path = Path(save_dir) / f"{model_name}.sql"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sql + "\n", encoding="utf-8")
    emit(f"  saved {out_path}")
    return model_name


def generate_model(
    prompt: str,
    save_dir: str = "examples",
    on_step: Optional[Callable[[str], None]] = None,
) -> GenerationResult:
    """Run the full agent loop for one natural-language request and save the
    resulting dbt model to save_dir/<slug>.sql.

    Dispatches to the Anthropic or Gemini implementation based on
    CALIBRATE_LLM_PROVIDER (default "anthropic"). on_step, if given, is
    called synchronously with one human-readable line after every real
    milestone (each LLM turn, each governed tool call with its actual
    measured duration, the final save) as it actually happens - not
    replayed afterward. dashboard/app.py uses this to stream the run live
    instead of showing a single "please wait" spinner.
    """
    provider = os.environ.get("CALIBRATE_LLM_PROVIDER", "anthropic").lower()
    if provider == "gemini":
        return _generate_with_gemini(prompt, save_dir, on_step)
    if provider == "anthropic":
        return _generate_with_anthropic(prompt, save_dir, on_step)
    raise ValueError(f"Unknown CALIBRATE_LLM_PROVIDER '{provider}' - use 'anthropic' or 'gemini'.")


def _generate_with_anthropic(
    prompt: str, save_dir: str, on_step: Optional[Callable[[str], None]]
) -> GenerationResult:
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

    _emit(f'agent.generate_model("{prompt}")  [anthropic: {_ANTHROPIC_MODEL}]')

    for turn in range(1, _MAX_TURNS + 1):
        _emit(f"  turn {turn}: calling {_ANTHROPIC_MODEL}...")
        t0 = time.perf_counter()
        response = client.messages.create(
            model=_ANTHROPIC_MODEL,
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

    model_name = _save_sql(prompt, sql, save_dir, _emit)
    return GenerationResult(sql=sql, model_name=model_name, turns=turn, tool_calls=tool_calls)


def _generate_with_gemini(
    prompt: str, save_dir: str, on_step: Optional[Callable[[str], None]]
) -> GenerationResult:
    from google import genai
    from google.genai import types

    def _emit(line: str) -> None:
        if on_step is not None:
            on_step(line)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env (see .env.example) - "
            "get a free one at https://aistudio.google.com/apikey"
        )

    reset_session_counters()
    client = genai.Client(api_key=api_key)

    # Gemini's FunctionDeclaration accepts a plain JSON schema directly via
    # parameters_json_schema - the exact same input_schema dicts the
    # Anthropic loop uses, no translation needed.
    function_declarations = [
        types.FunctionDeclaration(
            name=tool_def["name"],
            description=tool_def["description"],
            parameters_json_schema=tool_def["input_schema"],
        )
        for tool_def in TOOL_DEFINITIONS
    ]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[types.Tool(function_declarations=function_declarations)],
        # Automatic function calling would call our tool functions directly
        # from inside the SDK, bypassing governance/audit_log entirely -
        # disabled so every call goes through _dispatch_tool -> the same
        # @governed tools the Anthropic loop and the MCP server use.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    tool_calls: list[dict[str, Any]] = []
    final_text = ""

    _emit(f'agent.generate_model("{prompt}")  [gemini: {_GEMINI_MODEL}]')

    for turn in range(1, _MAX_TURNS + 1):
        _emit(f"  turn {turn}: calling {_GEMINI_MODEL}...")
        t0 = time.perf_counter()
        response = _call_gemini_with_retry(client, _GEMINI_MODEL, contents, config, _emit)
        _emit(f"  turn {turn}: response in {(time.perf_counter() - t0) * 1000:.0f}ms")

        candidate = response.candidates[0]
        contents.append(candidate.content)

        parts = candidate.content.parts or []
        function_calls = [p.function_call for p in parts if p.function_call is not None]
        text_parts = [p.text for p in parts if p.text]
        final_text = "\n".join(text_parts)

        if not function_calls:
            break

        response_parts = []
        for fc in function_calls:
            args = dict(fc.args or {})
            t1 = time.perf_counter()
            try:
                result = _dispatch_tool(fc.name, args)
                content_str = str(result)
                is_error = False
            except Exception as exc:  # noqa: BLE001 - surfaced back to the model as a tool error
                content_str = f"Error: {exc}"
                is_error = True
            duration_ms = (time.perf_counter() - t1) * 1000
            status = "ERROR" if is_error else "ok"
            arg_preview = ", ".join(f"{k}={v!r:.60}" for k, v in args.items() if k != "sql")
            _emit(f"    [governed] calibrate-agent -> {fc.name}({arg_preview}) ... {status} ({duration_ms:.0f}ms)")
            tool_calls.append({"tool": fc.name, "input": args, "error": is_error})
            response_parts.append(
                types.Part.from_function_response(name=fc.name, response={"result": content_str})
            )
        contents.append(types.Content(role="user", parts=response_parts))
    else:
        raise RuntimeError(f"Agent did not finish within {_MAX_TURNS} turns.")

    sql = _extract_sql(final_text)
    if not sql:
        raise RuntimeError(f"Agent finished without producing a ```sql block. Last response:\n{final_text}")

    model_name = _save_sql(prompt, sql, save_dir, _emit)
    return GenerationResult(sql=sql, model_name=model_name, turns=turn, tool_calls=tool_calls)
