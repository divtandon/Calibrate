-- Staging layer: one row per real order, cast and null-guarded. Every mart
-- in this project reads orders through here rather than the raw source
-- table, so a cleansing rule only has to be written once.
select
    o_orderkey as order_key,
    o_custkey as customer_key,
    o_orderdate as order_date,
    date_trunc('month', o_orderdate) as order_month,
    o_orderstatus as order_status,
    cast(o_totalprice as double) as total_price,
    o_orderpriority as order_priority,
    o_shippriority as ship_priority
from {{ source('tpch', 'orders') }}
where o_orderkey is not null
  and o_totalprice is not null
