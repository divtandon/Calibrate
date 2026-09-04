"""Unit tests for validation/drift_report.py's aggregation-type detection -
regression coverage for a real bug found validating an actual agent-
generated model: a "total order count by nation" model correctly computed
a COUNT metric, but reconciliation compared it against the revenue-sum
reference total and flagged a false 400%+ "mismatch". Fixed by sniffing
the aggregation function wrapping the metric column directly out of the
resolved SQL and picking the matching reference query.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validation.drift_report import _detect_metric_aggregation


def test_detects_sum_aggregation():
    sql = "select region, sum(o_totalprice) as metric_value from orders group by 1"
    assert _detect_metric_aggregation(sql, "metric_value") == "sum"


def test_detects_count_aggregation():
    sql = "select n.n_name as dimension, count(o.o_orderkey) as metric_value from orders o group by 1"
    assert _detect_metric_aggregation(sql, "metric_value") == "count"


def test_detects_avg_aggregation():
    sql = "select region, avg(o_totalprice) as metric_value from orders group by 1"
    assert _detect_metric_aggregation(sql, "metric_value") == "avg"


def test_case_and_whitespace_insensitive():
    sql = "select region,\n    SUM( o_totalprice )   AS   metric_value\nfrom orders group by 1"
    assert _detect_metric_aggregation(sql, "metric_value") == "sum"


def test_no_match_returns_none():
    sql = "select region, o_totalprice as metric_value from orders"
    assert _detect_metric_aggregation(sql, "metric_value") is None


def test_only_matches_the_named_column_not_others():
    # a sum() elsewhere in the query aliased to a different name must not
    # be mistaken for the metric column's own aggregation
    sql = "select region, sum(o_totalprice) as other_col, count(*) as metric_value from orders group by 1"
    assert _detect_metric_aggregation(sql, "metric_value") == "count"
