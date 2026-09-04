# Calibrate
*A grounded, governed, statistically-validated data pipeline agent*

## 1. The problem, and where it's coming from

Two separate, current, well-documented problems, both real as of 2026, both still unsolved by existing tooling. Not one invented one.

**Problem A: AI agents generate plausible code without verifying it against real system state, and nobody can see what they did.**

Atlassian's own engineering leadership published this directly: AI coding-agent adoption is up 65% across professional teams, but overall delivery velocity rose only 10-15%. Their stated diagnosis is that agents lack the real architectural context, decision history, and definition of "done" that only exists scattered across an organization's actual systems, and that ungoverned agent sessions disappear into terminals with no record of what happened or who reviewed it.
Source: "How we're evolving Jira for AI-native software development," Atlassian, Jul 15 2026. atlassian.com/blog/company-news/ai-sdlc

Snowflake built the same diagnosis into a product, independently. They acquired Natoma, an enterprise MCP platform, in May 2026, then shipped Cortex AI Gateway in July 2026: a centralized layer governing how AI agents access models, tools, MCP servers, and data, with policy, activity, and cost controls across 100+ MCP servers. Their coding agent, Cortex Code, is explicitly built to avoid ungrounded guessing by working against real schema, permissions, and semantic models instead of generic completion.
Sources: "Snowflake Unveils Cortex AI Gateway for Governing Enterprise Agents," Let's Data Science, Jul 28 2026. letsdatascience.com/news/snowflake-unveils-cortex-ai-gateway-for-governing-enterprise-d99a944a — "7 Snowflake Summit 2026 launches data engineers should know," Jun 11 2026. blog.invidelabs.com/snowflake-summit-2026-highlights-for-data-engineers/

**Problem B: even schema-correct AI-generated SQL and pipelines silently produce wrong data, and grounding against a real schema does not catch this.**

This is the specific gap the first problem doesn't cover, and it's the one this project is actually built around.

- A 2026 industry survey (426 respondents) found AI now touches production databases in 96.5% of organizations, while roughly two-thirds of respondents report data quality issues and nearly half worry specifically about *ungoverned* AI-generated SQL.
  Source: "Liquibase 2026 State of Database Change Governance Report," Business Wire, Mar 11 2026. businesswire.com/news/home/20260311497754/en/
- dbt Labs' own 2026 industry report states plainly that AI is scaling analytics output faster than governance can follow, and that the share of practitioners prioritizing trust in data over raw speed rose from 66% to 83% in a single year.
  Source: "2026 State of Analytics Engineering Report," dbt Labs. getdbt.com/resources/state-of-analytics-engineering-2026
- Data + AI observability company Monte Carlo analyzed 1,000 real troubleshooting investigations and found that while AI is reducing classic SQL syntax errors, data quality incidents are not going away, they're shifting to a different layer: logic and output correctness, not syntax.
  Source: "AI Is Eliminating SQL Errors. So Why Is Data Still Breaking?", Michael Segner, Data Science Collective, Mar 16 2026. medium.com/data-science-collective/has-ai-assisted-coding-made-data-quality-better-or-worse-0d3e650af103
- A 2026 benchmark found top models achieve roughly 78% execution accuracy on zero-shot text-to-SQL, meaning about one in five generated queries is wrong in ways that don't throw an error, they just quietly return the wrong answer.
  Source: "Don't Trust AI-Generated SQL Blindly," DEV Community, May 12 2026. dev.to/vivekdraxlr/dont-trust-ai-generated-sql-blindly-a-developers-validation-checklist-5f9g
- A documented real-world case: a silent logic error in AI-generated SQL ran undetected for three weeks and skewed a company's quarterly revenue figure by 11.7%. The query was schema-valid and executed cleanly the entire time.
  Source: "I trusted AI to write my SQL for 6 months. Here's what silently broke," Write A Catalyst, May 16 2026. medium.com/write-a-catalyst/i-trusted-ai-to-write-my-sql-for-6-months-heres-what-silently-broke-45c9d220606a

**The gap between A and B is the actual thesis of this project.** Schema grounding (what Cortex Code and DataHub-style catalogs solve) stops an agent from inventing a column that doesn't exist. It does not stop an agent from writing a query that runs cleanly, references only real columns, and still silently answers the wrong question, wrong GROUP BY, wrong join cardinality, a metric that quietly drifts. That second failure mode is common enough that dedicated open-source tooling already exists for it (section 4), and it's exactly where a background in verifying signal against a known baseline, rather than trusting that something "ran successfully," is a genuine, non-generic skill to bring.

## 2. What this project actually is

An agent that generates dbt models against a real Snowflake schema (so it can't invent columns, matching Cortex Code's own approach), and before any generated model is allowed to ship, its actual output gets checked statistically against a historical baseline, row counts, null rates, value distributions, not just "did it execute." Every generation and validation step is policy-checked and logged. New codebase throughout. Guardian and DataDoc are not dependencies; the underlying ideas (verify before generating, log every access) carry over as design principles, not as imported code.

## 3. Data source

Real Snowflake, not a mock. Every Snowflake account, including the free trial, ships a built-in `SNOWFLAKE_SAMPLE_DATA` database with the TPC-H benchmark schema (orders, customers, lineitem, and related tables), an industry-standard synthetic dataset built for exactly this kind of demonstration, officially documented and maintained by Snowflake itself.
Sources: "Sample data: TPC-H," Snowflake Documentation. docs.snowflake.com/en/user-guide/sample-data-tpch — "Sample data sets," Snowflake Documentation. docs.snowflake.com/en/user-guide/sample-data — Free trial signup: trial.snowflake.com

TPC-H's `ORDERS` table carries real order dates spanning several years, which gives a legitimate way to define a historical baseline period versus a recent period for the drift-detection validation, instead of needing invented time-series data.

No lab data, no proprietary company data, nothing that requires anyone's permission to use. This sidesteps the data-sourcing problem entirely instead of raising it.

**Implementation note:** Calibrate's default local backend runs the same TPC-H schema via DuckDB's official `tpch` dbgen extension, which generates the identical industry-standard benchmark tables locally with no account or credentials required. This is not a mock or fixture — it's the same synthetic dataset generator the TPC-H standard itself is built on. The Snowflake backend (`SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`) is a first-class, fully implemented alternative selected by one config flag (`CALIBRATE_BACKEND=snowflake`) plus real trial credentials — see `db/connection.py`.

## 4. Prior art, so the differentiation claim is honest

This category of tooling already exists in pieces. Citing it directly instead of pretending this is unprecedented:

- **Snowflake MCP servers already exist**, several independently built, confirming this exact integration point is real and active, not speculative:
  - Snowflake-Labs/snowflake-cortex-agent-mcp-server, github.com/Snowflake-Labs/snowflake-cortex-agent-mcp-server, published under Snowflake's own GitHub org (marked experimental, not an official product)
  - isaacwasserman/mcp-snowflake-server, github.com/isaacwasserman/mcp-snowflake-server, notable for an `append_insight` write-back pattern
  - snowflake-mcp/snowflake-mcp-server, github.com/snowflake-mcp/snowflake-mcp-server, notable for shipping a `check_data_quality` tool as a first-class MCP tool, direct precedent for this project's core idea
- **Statistical/data-quality validation for dbt already exists as tooling**, confirming Problem B is a recognized, actively-worked area, not a novel invention:
  - calogica/dbt-expectations, github.com/calogica/dbt-expectations, a Great Expectations-style validation package for dbt
  - elementary-data, dbt-native data observability with anomaly detection tests
  - Great Expectations itself, greatexpectations.io
- **Real Snowflake + dbt precedent against this exact sample data**: clausherther/dbt-tpch, github.com/clausherther/dbt-tpch, a working dbt project built directly against Snowflake's own TPC-H sample database.

None of these combine grounded generation, statistical output validation, and access governance into one agent-facing loop with an audit trail. That combination, not any single piece of it, is what's actually being built. Recommended approach given the timeline: adapt an existing open-source Snowflake MCP server (isaacwasserman/mcp-snowflake-server is the cleanest base) for the connection plumbing rather than rebuilding it from zero, and put the real engineering effort into the statistical validation layer and the governance layer, which is where the actual differentiation lives.

## 5. Architecture

```
Calibrate

  cli / demo
      |  "generate a dbt model for monthly revenue by region"
      v
  agent/core.py          (Anthropic tool-use agent loop, MCP-client wired)
      |  MCP tool calls
      v
  mcp_server/             (real MCP server, FastMCP-based)
      |-- get_schema(table_name)
      |-- get_historical_baseline(table, metric, period)   -> queried from real TPC-H order dates
      |-- run_generated_model(sql)                          -> executes against the real backend
      \-- flag_output_anomaly(model, note)                  -> write-back when validation fails
      |
      v
  validation/              <- the signature layer
      |-- baseline_check.py   row count / null rate / value distribution vs historical period
      \-- drift_report.py     produces the pass/fail + delta shown on the dashboard
      |
      v
  governance/               (policy engine)
      |-- policy.py           allow/deny per tool, per agent
      \-- audit_log.py        every call logged: who, what, allowed/blocked, cost

  dashboard/                NiceGUI, industrial style
```

## 6. Phased build plan

**Phase 0.** Repo scaffold. Connect to a real data backend (DuckDB TPC-H locally by default, real Snowflake free trial account when configured), confirm the TPC-H schema is queryable. Implement `get_schema` and `get_historical_baseline`. Checkpoint: show a real tool call returning real schema and a real baseline number.

**Phase 1.** Agent wiring. A natural-language request produces a real dbt model grounded in the real schema just queried. Checkpoint: one real generated `.sql` file, saved to `examples/`.

**Phase 2.** The validation layer. `run_generated_model` actually executes the generated SQL against the backend. `baseline_check.py` compares its output against the historical-period baseline (row count, null rate, key metric distribution) and produces a pass/flag verdict with a real delta number. Checkpoint: one model that passes, one deliberately-broken model (bad GROUP BY or join) that gets caught, both shown with real numbers, not invented ones.

*Phases 0-2 are the whole proof. Phase 3 is additive, not a blocker.*

**Phase 3.** Governance layer. `policy.py` and `audit_log.py`. Every MCP call in Phases 0-2 gets policy-checked and logged.

**Phase 4.** Dashboard: pipeline list, the drift chart, the governance strip, live telemetry.

## 7. Full source list

- Atlassian, "How we're evolving Jira for AI-native software development," Jul 15 2026 — atlassian.com/blog/company-news/ai-sdlc
- Let's Data Science, "Snowflake Unveils Cortex AI Gateway for Governing Enterprise Agents," Jul 28 2026 — letsdatascience.com/news/snowflake-unveils-cortex-ai-gateway-for-governing-enterprise-d99a944a
- invidelabs, "7 Snowflake Summit 2026 launches data engineers should know," Jun 11 2026 — blog.invidelabs.com/snowflake-summit-2026-highlights-for-data-engineers/
- Business Wire / Liquibase, "2026 State of Database Change Governance Report," Mar 11 2026 — businesswire.com/news/home/20260311497754/en/
- dbt Labs, "2026 State of Analytics Engineering Report" — getdbt.com/resources/state-of-analytics-engineering-2026
- Michael Segner / Data Science Collective, "AI Is Eliminating SQL Errors. So Why Is Data Still Breaking?", Mar 16 2026 — medium.com/data-science-collective/has-ai-assisted-coding-made-data-quality-better-or-worse-0d3e650af103
- DEV Community, "Don't Trust AI-Generated SQL Blindly," May 12 2026 — dev.to/vivekdraxlr/dont-trust-ai-generated-sql-blindly-a-developers-validation-checklist-5f9g
- Write A Catalyst, "I trusted AI to write my SQL for 6 months...", May 16 2026 — medium.com/write-a-catalyst/i-trusted-ai-to-write-my-sql-for-6-months-heres-what-silently-broke-45c9d220606a
- Snowflake Documentation, "Sample data: TPC-H" — docs.snowflake.com/en/user-guide/sample-data-tpch
- Snowflake Documentation, "Sample data sets" — docs.snowflake.com/en/user-guide/sample-data
- github.com/Snowflake-Labs/snowflake-cortex-agent-mcp-server
- github.com/isaacwasserman/mcp-snowflake-server
- github.com/snowflake-mcp/snowflake-mcp-server
- github.com/calogica/dbt-expectations
- github.com/elementary-data
- greatexpectations.io
- github.com/clausherther/dbt-tpch

Read the primary ones yourself before repeating any of this in an interview, this list is a starting point for verification, not a substitute for it.
