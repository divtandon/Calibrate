"""Small, dependency-free statistics helpers shared by baseline_check.py.
No numpy/scipy - the volumes here (hundreds to low thousands of grouped
rows) don't need it, and it keeps the validation layer's math auditable in
plain Python.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence


def mean(values: Sequence[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def stddev(values: Sequence[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    m = mean(values)
    variance = sum((v - m) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def null_rate(values: Sequence) -> float:
    if not values:
        return 0.0
    nulls = sum(1 for v in values if v is None)
    return nulls / len(values)


def z_score(sample_mean: float, baseline_mean: float, baseline_stddev: Optional[float]) -> Optional[float]:
    if baseline_stddev is None or baseline_stddev == 0:
        return None
    return (sample_mean - baseline_mean) / baseline_stddev
