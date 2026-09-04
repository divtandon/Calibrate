"""Tests for the governance policy engine, including a regression test for
the positional-argument bypass found and fixed while building Phase 2 (see
governance/guard.py) - a call like run_generated_model('DROP TABLE ...')
must be blocked exactly like the keyword form.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from governance.policy import check_policy, reset_session_counters
from governance.guard import governed


def test_unknown_tool_denied_by_default():
    decision = check_policy("not_a_real_tool", "demo-cli")
    assert decision.allowed is False


def test_unauthorized_actor_denied():
    decision = check_policy("get_schema", "someone-random")
    assert decision.allowed is False


def test_authorized_actor_allowed():
    decision = check_policy("get_schema", "demo-cli", table="orders")
    assert decision.allowed is True


def test_denied_sql_keyword_blocked_via_kwarg():
    decision = check_policy("run_generated_model", "demo-cli", sql="DROP TABLE orders")
    assert decision.allowed is False
    assert "DROP" in decision.reason


def test_safe_sql_allowed():
    decision = check_policy("run_generated_model", "demo-cli", sql="SELECT * FROM {{ source('tpch','orders') }}")
    assert decision.allowed is True


def test_governed_decorator_blocks_positional_sql_argument():
    """Regression test: governed() must bind positional args by name before
    checking policy, or a call like fn('DROP TABLE ...') bypasses the SQL
    keyword scan entirely because check_policy only ever saw kwargs.
    """

    @governed("run_generated_model")
    def fake_run_generated_model(sql: str) -> str:
        return "should not reach here"

    with pytest.raises(PermissionError):
        fake_run_generated_model("DROP TABLE orders", actor="demo-cli")


def test_governed_decorator_allows_safe_positional_call():
    @governed("run_generated_model")
    def fake_run_generated_model(sql: str) -> str:
        return "ok"

    assert fake_run_generated_model("SELECT 1", actor="demo-cli") == "ok"


def test_max_calls_per_session_enforced():
    reset_session_counters()
    for _ in range(100):
        decision = check_policy("get_schema", "demo-cli", table="orders")
        assert decision.allowed is True
    decision = check_policy("get_schema", "demo-cli", table="orders")
    assert decision.allowed is False
    reset_session_counters()
