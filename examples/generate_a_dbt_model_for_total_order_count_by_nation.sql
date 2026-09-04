-- Total order count by nation and order month
{{ config(materialized='view') }}

select
    n.n_name as dimension,
    date_trunc('month', o.o_orderdate) as order_month,
    count(o.o_orderkey) as metric_value
from {{ source('tpch', 'orders') }} o
join {{ source('tpch', 'customer') }} c on o.o_custkey = c.c_custkey
join {{ source('tpch', 'nation') }} n on c.c_nationkey = n.n_nationkey
group by 1, 2
