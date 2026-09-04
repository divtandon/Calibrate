-- A third correct model, grouped by customer market segment instead of a
-- geography table - shows the same three checks holding up across a
-- different dimension and a different join shape (straight to customer,
-- no nation/region hop) while still reconciling to the same grand total.
{{ config(materialized='view') }}

select
    c.c_mktsegment as segment,
    date_trunc('month', o.o_orderdate) as order_month,
    sum(o.o_totalprice) as total_revenue,
    count(distinct o.o_orderkey) as order_count
from {{ source('tpch', 'orders') }} o
join {{ source('tpch', 'customer') }} c on o.o_custkey = c.c_custkey
group by 1, 2
order by 1, 2
