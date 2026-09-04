"""The `governed` decorator: wraps a tool function so every call to it is
policy-checked and audit-logged, with no repeated boilerplate in
mcp_server/tools.py. This is the enforcement point Phase 3 adds on top of
the Phase 0-2 tools without changing their bodies.
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Any, Callable

from governance.audit_log import log_call
from governance.policy import check_policy


def governed(tool_name: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        # Bind by name regardless of how the caller passes arguments -
        # policy checks (e.g. scanning `sql` for denied keywords) must see
        # every argument, not just the ones that happened to arrive as
        # kwargs. A positional call bypassing the keyword scan is a real
        # governance hole, not a style nit.
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: Any, actor: str = "calibrate-agent", **kwargs: Any) -> Any:
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            all_kwargs = dict(bound.arguments)

            decision = check_policy(tool_name, actor, **all_kwargs)
            if not decision.allowed:
                log_call(actor, tool_name, all_kwargs, allowed=False, reason=decision.reason)
                raise PermissionError(f"[governance] blocked {tool_name}: {decision.reason}")

            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - deliberately broad, we re-raise
                duration_ms = (time.perf_counter() - start) * 1000
                log_call(actor, tool_name, all_kwargs, allowed=True, reason=decision.reason, duration_ms=duration_ms, error=str(exc))
                raise
            duration_ms = (time.perf_counter() - start) * 1000
            log_call(actor, tool_name, all_kwargs, allowed=True, reason=decision.reason, duration_ms=duration_ms)
            return result

        return wrapper

    return decorator
