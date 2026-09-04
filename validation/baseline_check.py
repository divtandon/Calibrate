"""The statistical validation core: takes a generated model's actual output
and decides VERIFIED or FLAGGED using three real checks, not "did it run."

1. Grouping-key uniqueness - are the declared dimension + period columns
   actually unique per row? A model that groups by the wrong key (or
   forgets a GROUP BY column) produces duplicate rows here, silently.
2. Reconciliation - does SUM(metric) across the whole output match an
   independently-computed control total for the same universe of orders?
   A join that fans out (e.g. joining to lineitem before aggregating
   o_totalprice) inflates this without ever throwing an error.
3. Temporal drift - splitting the real output into TPC-H's historical vs
   recent order-date periods (validation/config.py), does the recent
   period's row-count-per-period, null rate, and value distribution stay
   within the same statistical shape as the historical period?

Every number below is computed from the actual rows passed in - nothing is
invented or hardcoded per-run.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from validation import stats_utils
from validation.config import (
    CUTOFF_MONTH,
    NULL_RATE_VARIANCE_THRESHOLD,
    RECONCILIATION_TOLERANCE,
    ROW_COUNT_DELTA_THRESHOLD,
    Z_SCORE_THRESHOLD,
)


@dataclass
class PeriodStats:
    row_count: int
    distinct_periods: int
    avg_rows_per_period: Optional[float]
    null_rate: float
    mean: Optional[float]
    stddev: Optional[float]


@dataclass
class BaselineCheckResult:
    verdict: str  # "VERIFIED" or "FLAGGED"
    flags: list[str] = field(default_factory=list)

    total_row_count: int = 0
    duplicate_group_rows: int = 0

    model_total: Optional[float] = None
    reference_total: Optional[float] = None
    reconciliation_delta_pct: Optional[float] = None

    baseline: Optional[PeriodStats] = None
    recent: Optional[PeriodStats] = None
    row_count_delta_pct: Optional[float] = None
    null_rate_variance: Optional[float] = None
    distribution_drift_sigma: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["baseline"] = self.baseline.__dict__ if self.baseline else None
        d["recent"] = self.recent.__dict__ if self.recent else None
        return d


def _period_key(value: Any) -> str:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return f"{value.year:04d}-{value.month:02d}"
    return str(value)[:7]


def _period_stats(rows_slice: list[dict[str, Any]], period_col: str, metric_col: str) -> PeriodStats:
    metric_values = [r[metric_col] for r in rows_slice]
    periods = {_period_key(r[period_col]) for r in rows_slice}
    row_count = len(rows_slice)
    distinct_periods = len(periods) or 1
    return PeriodStats(
        row_count=row_count,
        distinct_periods=len(periods),
        avg_rows_per_period=row_count / distinct_periods if distinct_periods else None,
        null_rate=stats_utils.null_rate(metric_values),
        mean=stats_utils.mean([v for v in metric_values if v is not None]),
        stddev=stats_utils.stddev([v for v in metric_values if v is not None]),
    )


def check_model_output(
    columns: list[str],
    rows: list[tuple],
    period_col: str,
    metric_col: str,
    dimension_cols: list[str],
    reference_total: Optional[float] = None,
) -> BaselineCheckResult:
    idx = {c: i for i, c in enumerate(columns)}
    for required in [period_col, metric_col, *dimension_cols]:
        if required not in idx:
            raise ValueError(f"Column '{required}' not present in model output columns {columns}")

    def _as_float(v: Any) -> Any:
        return float(v) if v is not None else None

    dict_rows = [
        {c: (_as_float(row[idx[c]]) if c == metric_col else row[idx[c]]) for c in columns}
        for row in rows
    ]
    flags: list[str] = []

    # 1. grouping-key uniqueness
    group_keys = [tuple(r[c] for c in [*dimension_cols, period_col]) for r in dict_rows]
    distinct_keys = len(set(group_keys))
    duplicate_group_rows = len(group_keys) - distinct_keys
    if duplicate_group_rows > 0:
        flags.append(
            f"duplicate_grouping_keys: {duplicate_group_rows} row(s) share a ({', '.join(dimension_cols)}, {period_col}) "
            f"key that should be unique - the GROUP BY is likely missing a column."
        )

    # 2. reconciliation
    model_total = sum(r[metric_col] for r in dict_rows if r[metric_col] is not None)
    reconciliation_delta_pct = None
    if reference_total is not None and reference_total != 0:
        reconciliation_delta_pct = abs(model_total - reference_total) / abs(reference_total)
        if reconciliation_delta_pct > RECONCILIATION_TOLERANCE:
            flags.append(
                f"reconciliation_mismatch: model total {model_total:,.2f} differs from the independent "
                f"reference total {reference_total:,.2f} by {reconciliation_delta_pct:.2%} "
                f"(tolerance {RECONCILIATION_TOLERANCE:.2%}) - likely a join fan-out inflating the sum."
            )

    # 3. temporal drift
    baseline_rows = [r for r in dict_rows if _period_key(r[period_col]) < CUTOFF_MONTH]
    recent_rows = [r for r in dict_rows if _period_key(r[period_col]) >= CUTOFF_MONTH]

    baseline_stats = _period_stats(baseline_rows, period_col, metric_col) if baseline_rows else None
    recent_stats = _period_stats(recent_rows, period_col, metric_col) if recent_rows else None

    row_count_delta_pct = None
    null_rate_variance = None
    distribution_drift_sigma = None

    if baseline_stats and recent_stats:
        if baseline_stats.avg_rows_per_period:
            row_count_delta_pct = (
                recent_stats.avg_rows_per_period - baseline_stats.avg_rows_per_period
            ) / baseline_stats.avg_rows_per_period
            if abs(row_count_delta_pct) > ROW_COUNT_DELTA_THRESHOLD:
                flags.append(
                    f"row_count_drift: average rows/period moved from {baseline_stats.avg_rows_per_period:.1f} "
                    f"(baseline) to {recent_stats.avg_rows_per_period:.1f} (recent), a {row_count_delta_pct:+.1%} "
                    f"change (threshold {ROW_COUNT_DELTA_THRESHOLD:.0%})."
                )

        null_rate_variance = recent_stats.null_rate - baseline_stats.null_rate
        if abs(null_rate_variance) > NULL_RATE_VARIANCE_THRESHOLD:
            flags.append(
                f"null_rate_drift: {metric_col} null rate moved from {baseline_stats.null_rate:.2%} (baseline) "
                f"to {recent_stats.null_rate:.2%} (recent), a {null_rate_variance:+.2%} change "
                f"(threshold {NULL_RATE_VARIANCE_THRESHOLD:.0%})."
            )

        if recent_stats.mean is not None and baseline_stats.mean is not None:
            distribution_drift_sigma = stats_utils.z_score(recent_stats.mean, baseline_stats.mean, baseline_stats.stddev)
            if distribution_drift_sigma is not None and abs(distribution_drift_sigma) > Z_SCORE_THRESHOLD:
                flags.append(
                    f"distribution_drift: recent-period mean {metric_col} ({recent_stats.mean:,.2f}) is "
                    f"{distribution_drift_sigma:+.2f} standard deviations from the baseline mean "
                    f"({baseline_stats.mean:,.2f}), threshold {Z_SCORE_THRESHOLD:.1f}σ."
                )

    verdict = "FLAGGED" if flags else "VERIFIED"

    return BaselineCheckResult(
        verdict=verdict,
        flags=flags,
        total_row_count=len(rows),
        duplicate_group_rows=duplicate_group_rows,
        model_total=model_total,
        reference_total=reference_total,
        reconciliation_delta_pct=reconciliation_delta_pct,
        baseline=baseline_stats,
        recent=recent_stats,
        row_count_delta_pct=row_count_delta_pct,
        null_rate_variance=null_rate_variance,
        distribution_drift_sigma=distribution_drift_sigma,
    )
