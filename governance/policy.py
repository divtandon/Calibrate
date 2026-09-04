"""Allow/deny policy engine for every MCP tool call Calibrate makes.

Every tool in mcp_server/tools.py is wrapped with `governed()`, which calls
`check_policy()` before the tool body runs. A denied call never touches the
database - the agent gets a PermissionError back and the denial itself is
still written to the audit log, so a blocked action is exactly as visible as
an allowed one.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_POLICY_PATH = Path(__file__).parent / "policy.yaml"
_lock = threading.Lock()
_call_counts: dict[tuple[str, str], int] = {}


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str


def _load_policy() -> dict[str, Any]:
    with open(_POLICY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def reset_session_counters() -> None:
    """Call at the start of a demo run so per-session call limits reset."""
    with _lock:
        _call_counts.clear()


def check_policy(tool_name: str, actor: str, **kwargs: Any) -> PolicyDecision:
    policy = _load_policy()
    tool_rules = policy.get("tools", {}).get(tool_name)

    if tool_rules is None:
        return PolicyDecision(False, f"No policy entry exists for tool '{tool_name}' - deny by default.")

    allowed_actors = tool_rules.get("allowed_actors", [])
    if allowed_actors and actor not in allowed_actors:
        return PolicyDecision(False, f"Actor '{actor}' is not permitted to call '{tool_name}'.")

    max_calls = tool_rules.get("max_calls_per_session")
    if max_calls is not None:
        key = (tool_name, actor)
        with _lock:
            count = _call_counts.get(key, 0) + 1
            _call_counts[key] = count
        if count > max_calls:
            return PolicyDecision(False, f"Actor '{actor}' exceeded max_calls_per_session ({max_calls}) for '{tool_name}'.")

    if tool_name == "run_generated_model":
        sql = (kwargs.get("sql") or "").upper()
        for keyword in tool_rules.get("denied_sql_keywords", []):
            if re.search(re.escape(keyword.upper()), sql):
                return PolicyDecision(False, f"Generated SQL contains denied keyword '{keyword.strip()}' - models may only read data.")

    return PolicyDecision(True, "ok")
