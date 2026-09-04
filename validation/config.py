"""Shared constants for splitting TPC-H's real order-date history into a
historical baseline period and a recent period. The dataset spans
1992-01-01 through 1998-08-02 (scale factor 1); this cutoff gives a ~5.5
year baseline and a ~1 year recent window, both with enough rows for the
statistics in baseline_check.py to be meaningful rather than noisy.
"""

BASELINE_START = "1992-01-01"
BASELINE_END_EXCLUSIVE = "1997-07-01"   # baseline period: [BASELINE_START, this)
RECENT_END_EXCLUSIVE = "1998-08-03"     # recent period: [BASELINE_END_EXCLUSIVE, this)
CUTOFF_MONTH = "1997-07"                # 'YYYY-MM' string form, used to split model output rows

# Flag thresholds - real, fixed, documented, not tuned after the fact.
Z_SCORE_THRESHOLD = 3.0            # distribution drift, in standard deviations (standard 3-sigma rule)
NULL_RATE_VARIANCE_THRESHOLD = 0.02  # 2 percentage points
ROW_COUNT_DELTA_THRESHOLD = 0.25     # 25%, normalized per-period
RECONCILIATION_TOLERANCE = 0.005     # 0.5% - model total vs independent reference total
