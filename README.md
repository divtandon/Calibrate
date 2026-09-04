# Calibrate

*A grounded, governed, statistically-validated data pipeline agent.*

Calibrate generates dbt models against a real TPC-H benchmark schema, then
statistically validates each model's actual output - row counts, null
rates, value distributions, and an independent reconciliation total -
against real historical data before calling it verified. See
[PROJECT_SPEC.md](PROJECT_SPEC.md) for the full problem statement, sourcing,
and architecture this was built from.

The whole thesis in one sentence: schema grounding stops an agent from
inventing a column that doesn't exist; it does not stop the agent from
writing a query that runs cleanly, references only real columns, and still
silently returns the wrong answer. Calibrate catches the second failure
mode, not just the first.

## What's real here

- **Real data.** The default backend runs DuckDB's own `tpch` dbgen
  extension at scale factor 1 - the exact same TPC-H benchmark schema and
  generator Snowflake's `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` ships on every
  account, including the free trial. 1.5M orders, 6M lineitem rows, real
  order dates spanning 1992-1998. A first-class Snowflake backend behind
  the same interface is one config flag away (`CALIBRATE_BACKEND=snowflake`
  + real trial credentials in `.env`).
- **Real governance.** Every tool call - allowed or blocked - is policy
  checked and audit logged before it touches data. `governance/policy.yaml`
  controls what's allowed; `data/calibrate_state.db`'s `audit_log` table is
  the actual record. During development this caught a real bug: the SQL
  keyword filter only inspected keyword arguments, so a positional
  `run_generated_model('DROP TABLE ...')` call bypassed it entirely. Fixed
  in `governance/guard.py` by binding every call through the wrapped
  function's real signature before the policy check runs - see
  `tests/test_policy.py::test_governed_decorator_blocks_positional_sql_argument`
  for the regression test.
- **Real validation, not "did it run."** `validation/baseline_check.py`
  runs three checks against a generated model's actual output: grouping-key
  uniqueness, an independent reconciliation total, and historical-vs-recent
  drift (row count, null rate, distribution z-score). `examples/` has one
  correct model (VERIFIED, its total reconciles exactly) and one
  deliberately broken one - a join through `lineitem` that fans out and
  silently inflates revenue by ~400%, schema-valid and cleanly executing
  the entire time, exactly the Problem B failure mode PROJECT_SPEC.md is
  built around.

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate            # or: source .venv/bin/activate
pip install -r requirements-dev.txt

# Phase 0: generate the real local TPC-H dataset (one-time, ~2 seconds)
python -m cli.demo setup

# Phase 0-2 checkpoint in one shot: real schema call, real baseline call,
# then validate the correct model and the deliberately-broken one
python -m cli.demo run-demo

# see what the governance layer actually logged
python -m cli.demo audit

# run the test suite
pytest tests/ -v

# the dashboard
python -m dashboard.app     # http://localhost:8080
```

No API keys or accounts are required for any of the above - the local
DuckDB backend needs nothing.

### Using the generation agent

`python -m cli.demo generate "generate a dbt model for monthly revenue by region"`
requires `ANTHROPIC_API_KEY` (get one at
console.anthropic.com/settings/keys). Copy `.env.example` to `.env` and set
it. The agent calls `get_schema` and `get_historical_baseline` to ground
itself in the real schema, calls `run_generated_model` to self-test its
draft, then saves the final SQL to `examples/`. Every one of those calls is
governed and audit-logged identically to the pre-built examples.

### Pointing at a real Snowflake account instead

1. Sign up at [trial.snowflake.com](https://trial.snowflake.com) (free).
   `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` ships on every account already.
2. In `.env`: `CALIBRATE_BACKEND=snowflake`, plus `SNOWFLAKE_ACCOUNT`,
   `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD` (see `.env.example` for the rest).
3. `python -m cli.demo setup` now just confirms the connection - no data
   generation needed, the sample data is already there.

### Real dbt, not just SQL files

`dbt_project/` is a genuine dbt project (staging + marts, `sources.yml`,
native dbt tests, a `dbt_expectations` check) that builds and passes
against the live data:

```bash
export DUCKDB_PATH="$(pwd)/data/calibrate.duckdb"     # absolute path, dbt runs from dbt_project/
dbt run  --project-dir dbt_project --profiles-dir dbt_project/profiles
dbt test --project-dir dbt_project --profiles-dir dbt_project/profiles
```

Note: `db/connection.py`'s runtime backend opens the DuckDB file
**read-only** by design (see below) - run `dbt run` when nothing else is
querying the file, not concurrently with the dashboard.

## Architecture

```
cli / demo, dashboard
      |
      v
agent/core.py          Anthropic tool-use loop, grounds itself via governed tool calls
      |
      v
mcp_server/             real FastMCP server + the four governed tools:
  |-- get_schema
  |-- get_historical_baseline
  |-- run_generated_model
  \-- flag_output_anomaly
      |
      v
validation/              baseline_check.py's three checks + drift_report.py orchestration
      |
      v
governance/               policy.py (allow/deny) + audit_log.py (every call, logged)
      |
      v
db/                        DuckDB (default, real TPC-H) or Snowflake, same interface
```

## Why the DuckDB backend is read-only

Early on, `db/connection.py` lazily generated the local TPC-H data inside
the runtime connection on first use. Running the dashboard and a CLI
command at the same time raced two writable connections against the same
file, and `customer`/`nation`/`region` ended up at exactly 2x their correct
row counts (`orders`/`lineitem` were unaffected - the race window closed
before generation reached them). `scripts/setup_local_data.py` is now the
one deliberate writer, verifies exact scale-factor-1 row counts, and
self-heals by dropping and regenerating if they don't match. Every runtime
connection (`db/connection.py`) opens the file `read_only=True`, so any
number of Calibrate processes can query it concurrently with no race
possible - and it's a second, connection-level line of defense on top of
the SQL-keyword policy filter against a generated model ever writing to the
underlying data.

## Project layout

| Path | What it is |
|---|---|
| `db/` | Backend abstraction: `DuckDBBackend` (default, real local TPC-H) and `SnowflakeBackend`, same interface |
| `governance/` | `policy.py`, `audit_log.py`, the `governed()` decorator, `policy.yaml` |
| `mcp_server/` | The four governed tools + a real FastMCP server exposing them over stdio |
| `agent/` | The Anthropic tool-use generation loop and its system prompt/tool schemas |
| `validation/` | `baseline_check.py`'s statistics, `drift_report.py`'s orchestration, `results_store.py` |
| `dbt_project/` | A real, passing dbt project against the live data |
| `examples/` | One verified generated model, one deliberately broken one |
| `cli/demo.py` | The command-line entry point tying everything together |
| `dashboard/app.py` | NiceGUI dashboard reading real state, can trigger real runs live |
| `scripts/` | `setup_local_data.py` (the one writer), `verify_connection.py` (Phase 0 checkpoint) |
| `tests/` | Unit tests for the validation statistics and the governance policy engine |

## Prior art

This category of tooling already exists in pieces - Snowflake MCP servers,
dbt-expectations, Great Expectations, elementary. None of them combine
grounded generation, statistical output validation, and access governance
into one agent-facing loop with an audit trail. See PROJECT_SPEC.md section
4 for the honest accounting of what's genuinely novel here versus what's
recombined from existing tools.
