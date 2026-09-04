-- Same shape as monthly_revenue_by_region.sql but grouped by nation instead
-- of region - a second, independently-correct model to show the catalog
-- isn't just one lucky example. VERIFIED: grain is (nation, order_month),
-- one row each, and the total reconciles to the same independent control
-- sum every revenue-by-X model in this project must match.
{{ config(materialized='view') }}

select
    n.n_name as nation,
    date_trunc('month', o.o_orderdate) as order_month,
    sum(o.o_totalprice) as total_revenue,
    count(distinct o.o_orderkey) as order_count
from {{ source('tpch', 'orders') }} o
join {{ source('tpch', 'customer') }} c on o.o_custkey = c.c_custkey
join {{ source('tpch', 'nation') }} n on c.c_nationkey = n.n_nationkey
group by 1, 2
order by 1, 2
