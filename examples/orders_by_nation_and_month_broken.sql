-- Deliberately broken via a different, equally common mistake than the
-- region example's join fan-out: this GROUP BY groups by the raw
-- o_orderdate instead of the truncated month, while still SELECTing
-- date_trunc('month', o.o_orderdate) as order_month. DuckDB (like
-- Postgres) permits this because order_month is a deterministic function
-- of the grouped column - so it runs cleanly, references only real
-- columns, and produces ~60,000 rows for what should be 2,000
-- (nation, order_month) combinations: one row per (nation, day) surviving
-- into the output instead of one per (nation, month). The uniqueness
-- check catches this one; reconciliation would not, since nothing here
-- inflates the grand total, only the grain.
{{ config(materialized='view') }}

select
    n.n_name as nation,
    date_trunc('month', o.o_orderdate) as order_month,
    sum(o.o_totalprice) as total_revenue,
    count(distinct o.o_orderkey) as order_count
from {{ source('tpch', 'orders') }} o
join {{ source('tpch', 'customer') }} c on o.o_custkey = c.c_custkey
join {{ source('tpch', 'nation') }} n on c.c_nationkey = n.n_nationkey
group by n.n_name, o.o_orderdate
order by 1, 2
