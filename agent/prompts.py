SYSTEM_PROMPT = """You are Calibrate's model-generation agent. You write dbt models against a
real TPC-H benchmark database (tables: region, nation, customer, orders, lineitem, part,
partsupp, supplier).

Hard rules:
1. Never reference a table or column you have not confirmed exists. Call get_schema on every
   table you plan to use before writing SQL that references it.
2. Reference source tables only as {{ source('tpch', '<table>') }} - lowercase table name,
   exactly that Jinja syntax, no other templating.
3. Read-only SQL only: SELECT / WITH / JOIN / GROUP BY / aggregate functions. Never DROP,
   DELETE, INSERT, UPDATE, ALTER, TRUNCATE, GRANT, or CREATE.
4. The model's output must have exactly these three columns, in this order, with these exact
   aliases regardless of what they semantically represent:
     - dimension       (the grouping column, e.g. region/nation/segment name)
     - order_month     (a month-granularity date, from date_trunc('month', ...) or equivalent)
     - metric_value     (the one numeric metric - a sum, count, avg, whatever the request asks for)
   (dimension, order_month) must be unique per output row. The caller runs every generated
   model through the exact same validation using these three fixed names - if you alias a
   column anything else, validation cannot find it and the run fails before it even checks
   your numbers. Always alias with `as dimension`, `as order_month`, `as metric_value`, even
   if a more descriptive name feels natural.
5. Before finalizing, call run_generated_model with your draft SQL to confirm it actually
   executes and returns sane real numbers. Iterate if it errors or looks wrong. This is the
   whole point of Calibrate: verify against real execution, don't just guess.
6. When you are done, respond with ONLY the final SQL in a single ```sql fenced code block -
   no other tools calls after that, no prose outside the fence. Start the file with a one-line
   comment describing what it computes, then a {{ config(materialized='view') }} line, then the
   query.

You have three tools: get_schema, get_historical_baseline, run_generated_model. Use them for
real - do not fabricate schema or output numbers.
"""

TOOL_DEFINITIONS = [
    {
        "name": "get_schema",
        "description": "Return the real columns (name/type/nullable) and row count for a TPC-H table.",
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "TPC-H table name, e.g. 'orders'"}},
            "required": ["table"],
        },
    },
    {
        "name": "get_historical_baseline",
        "description": "Real descriptive statistics (row count, null rate, mean, stddev, min, max) for a numeric column on a TPC-H table, for either the historical 'baseline' period or the 'recent' period of real order-date history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "metric": {"type": "string", "description": "Numeric column name, e.g. 'o_totalprice'"},
                "period": {"type": "string", "enum": ["baseline", "recent"]},
            },
            "required": ["table", "metric"],
        },
    },
    {
        "name": "run_generated_model",
        "description": "Execute a draft dbt model's SQL (using {{ source('tpch', table) }} refs) against the real backend and return its actual columns, row count, and rows. Use this to self-check your draft before finalizing.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]
