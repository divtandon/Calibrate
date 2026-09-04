"""The catalog of demo models cli/demo.py's run-demo and the dashboard's
'Run Phase 0-2 Demo' button both validate. One shared list so the two
surfaces never drift out of sync with each other.
"""

from __future__ import annotations

from typing import TypedDict


class DemoModel(TypedDict):
    name: str
    path: str
    period_col: str
    metric_col: str
    dimension_cols: list[str]


DEMO_MODELS: list[DemoModel] = [
    {
        "name": "monthly_revenue_by_region",
        "path": "examples/monthly_revenue_by_region.sql",
        "period_col": "order_month",
        "metric_col": "total_revenue",
        "dimension_cols": ["region"],
    },
    {
        "name": "monthly_revenue_by_region_broken",
        "path": "examples/monthly_revenue_by_region_broken.sql",
        "period_col": "order_month",
        "metric_col": "total_revenue",
        "dimension_cols": ["region"],
    },
    {
        "name": "orders_by_nation_and_month",
        "path": "examples/orders_by_nation_and_month.sql",
        "period_col": "order_month",
        "metric_col": "total_revenue",
        "dimension_cols": ["nation"],
    },
    {
        "name": "orders_by_nation_and_month_broken",
        "path": "examples/orders_by_nation_and_month_broken.sql",
        "period_col": "order_month",
        "metric_col": "total_revenue",
        "dimension_cols": ["nation"],
    },
    {
        "name": "customer_segment_monthly_revenue",
        "path": "examples/customer_segment_monthly_revenue.sql",
        "period_col": "order_month",
        "metric_col": "total_revenue",
        "dimension_cols": ["segment"],
    },
]
