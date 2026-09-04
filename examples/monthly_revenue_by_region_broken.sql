-- Deliberately broken, for the Phase 2 checkpoint: schema-valid, executes
-- cleanly, references only real columns - and silently wrong anyway. This
-- is Problem B from PROJECT_SPEC.md made concrete: joining orders to
-- lineitem before summing o_totalprice fans each order out across its
-- lineitem rows, so total_revenue is inflated by roughly the average
-- lineitem-count per order. Row counts and grouping stay perfectly valid
-- (still one row per region/month), which is exactly why a "did it run
-- without error" check would never catch this - only reconciling the
-- grand total against an independent control catches it.
{{ config(materialized='view') }}

select
    r.r_name as region,
    date_trunc('month', o.o_orderdate) as order_month,
    sum(o.o_totalprice) as total_revenue,
    count(distinct o.o_orderkey) as order_count
from {{ source('tpch', 'orders') }} o
join {{ source('tpch', 'lineitem') }} l on o.o_orderkey = l.l_orderkey
join {{ source('tpch', 'customer') }} c on o.o_custkey = c.c_custkey
join {{ source('tpch', 'nation') }} n on c.c_nationkey = n.n_nationkey
join {{ source('tpch', 'region') }} r on n.n_regionkey = r.r_regionkey
group by 1, 2
order by 1, 2
