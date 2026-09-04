-- Monthly revenue by region. Grain is one row per (region, order_month) -
-- that's the invariant validation/baseline_check.py's uniqueness check
-- enforces, and what distinguishes this from monthly_revenue_by_region_broken.sql.
select
    r.r_name as region,
    o.order_month,
    sum(o.total_price) as total_revenue,
    count(distinct o.order_key) as order_count
from {{ ref('stg_orders_cleansed') }} o
join {{ source('tpch', 'customer') }} c on o.customer_key = c.c_custkey
join {{ source('tpch', 'nation') }} n on c.c_nationkey = n.n_nationkey
join {{ source('tpch', 'region') }} r on n.n_regionkey = r.r_regionkey
group by 1, 2
order by 1, 2
