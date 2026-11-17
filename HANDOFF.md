# Agent Handoff Brief — SnowLangGraph

**Date:** 2026-11-16  
**Workspace:** `/Users/yourbabes/dev/SnowLearn/SnowLangGraph`  
**Git Branch / HEAD:** `main` @ `0d17816` (with uncommitted docs, and GitNexus files—see §5)

---

## 1. Environment & Runtime State

- **Python Virtualenv:** `.venv` (CPython `3.12.4`, managed via `uv`).
  - Critical dependency pins already resolved (`uv pip check` passes across `162` packages):
    - `sqlalchemy==2.0.57` (`<2.1`) so `snowflake-sqlalchemy==1.11.1` / `trulens-connectors-snowflake` import cleanly.
    - `cryptography==46.0.0` (`<47`) so `pyOpenSSL==25.3.0` / `snowflake-connector-python==3.18.1` work alongside `langgraph-api==0.13.3` (`langgraph-cli[inmem]`).
- **Environment File (`.env`, gitignored — DO NOT commit or print secrets):**
  - `SNOWFLAKE_ACCOUNT=<see local .env>`, `SNOWFLAKE_USER=<see local .env>`, `SNOWFLAKE_PASSWORD=<PAT>` (user attached Snowflake network policy), `SNOWFLAKE_WAREHOUSE=SNOWFLAKE_LEARNING_WH`, `SNOWFLAKE_ROLE=ACCOUNTADMIN`, `SNOWFLAKE_DATABASE=CUSTOMER_INTELLIGENCE_DB`, `SNOWFLAKE_SCHEMA=PUBLIC`, `LANGSMITH_API_KEY=<configured>`, `LANGSMITH_TRACING=true`.
- **Snowflake login: WORKING again (checked 2026-11-17 01:00 PST).** It failed at 00:20 PST with `250001: Network policy is required`. It recovered with no change from us. The cause is unconfirmed. It may be the PAT's time-limited network-policy bypass. If it fails again, fix in Snowsight (new PAT, or a network policy on the user). Live smoke check and chart query both passed after the recovery.
- **Dev Server: NOT running** (checked 2026-11-17; the old one on port 2024 had a dead terminal and failed every run). Start it: `.venv/bin/langgraph dev --allow-blocking --no-browser`. Then check `curl http://127.0.0.1:2024/ok` returns `{"ok":true}`.
  - Registered graph: `supervisor_workflow` (`langgraph.json` → `./studio_app.py:app`)
  - LangSmith Studio URL: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

---

## 2. Snowflake Trial Org State (account id is in the local `.env`; region `AWS_US_EAST_2`)

### Active & Verified
- **`CUSTOMER_INTELLIGENCE_DB.PUBLIC`**:
  - Tables loaded from local CSVs: `CUSTOMERS` (`10,000` rows), `USAGE_EVENTS` (`101,711` rows), `SUPPORT_TICKETS` (`8,954` rows), `CHURN_EVENTS` (`703` rows).
  - Semantic Views (`sql/03_setup_semantic_views.sql`): `CUSTOMER_BEHAVIOR_ANALYST`, `STRATEGIC_RESEARCH_ANALYST`.
  - Telemetry View (`sql/06_setup_observability_view.sql`): `AGENT_OBSERVABILITY_LOGS` over `TABLE(CUSTOMER_INTELLIGENCE_DB.INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT => 10000))` (raised from 500 and applied in Snowflake).
- **`SNOWFLAKE_INTELLIGENCE.AGENTS`**:
  - Agent DDL created (`sql/05_setup_cortex_agents.sql`): `CONTENT_AGENT`, `DATA_ANALYST_AGENT`, `RESEARCH_AGENT`.
- **Working AI Endpoints on Trial:**
  - **Cortex Analyst REST API (`/api/v2/cortex/analyst/message`)** and **`SEMANTIC_VIEW(...)` SQL queries** work natively.

### Blocked on Unpaid Self-Service Trial (Important!)
- `AI_COMPLETE`, `AI_EMBED`, `EMBED_TEXT_*` (`sql/02_setup_cortex_search.sql`), and `sql/04_setup_udfs.sql` fail with `399258 (0A000): ... not available for trial accounts.`
- `Cortex Inference` REST returns `403 Forbidden`; `Cortex Agent :run` returns `399504: Access denied for trial accounts.`
- New accounts also block legacy `SNOWFLAKE.CORTEX.COMPLETE` (`399270`) used by default in `langchain-snowflake==0.2.3`'s `ChatSnowflake` (`_use_rest_api = False`).
- The old mismatch between `sql/04` (functions) and `sql/05` (`*_V2` procedures) is fixed: `sql/05` now uses `type: "function"` with the real function names. It is untested until the account is upgraded.

---

## 3. Architecture & Implemented Observability

- **The app** (`studio_app.py` entry; code in `supervisor.py`, `snowflake_client.py`, `observability.py`) runs in **Trial-Compatible Mode** (`SNOWFLAKE_USE_CORTEX_AGENTS=false` default):
  - `DATA_ANALYST_AGENT` & `RESEARCH_AGENT` call Cortex Analyst REST API against the Semantic Views and execute generated SQL via `execute_snowflake_sql()`.
  - `CONTENT_AGENT` queries `SUPPORT_TICKETS` with parameterized SQL (`run_content_sql_search()`).
  - `supervisor_node()` performs deterministic planning (`_build_trial_plan()`), routing (`Command(goto=...)`), and synthesis (`_synthesize_trial_response()`).
  - **Observability**:
    - Nested LangSmith `@traceable` spans (`cortex_analyst_text_to_sql`, `snowflake_support_tickets_search`, `snowflake_sql_execute`).
    - `execute_snowflake_sql()` uses `df.collect(block=False)` to capture `sfqid` with zero extra round-trips and sends the QUERY_TAG with each statement (statement parameter; never changes the session) (`{"app": "snowlanggraph", "trace_id", "agent", "step", "tool"}`, reset in `finally`).
    - `emit_langsmith_feedback()` sends `execution_success`, `non_empty_results`, `zero_errors`, and `step_count` on synthesis.
- **`evaluate_observability.py`**:
  - Runs LangSmith dataset evals (`SnowLangGraph Customer Intelligence Eval`, latest experiment `snowlanggraph-trial-c05502ee`), TruLens `TruGraph` OTEL recording (`default.sqlite`, gitignored), and queries `AGENT_OBSERVABILITY_LOGS`.
  - *Verification note:* re-run on 2026-11-17 after the module split: exit 0, 4/4 routes correct, TruLens 1 record, audit view 10 rows.

---

## 4. Repo Artifacts, Knowledge Graph & Code Intelligence

- **Documentation:**
  - `DEMO_SUMMARY_AND_TALK_TRACK.md` — 1-page executive summary & talk track
  - `DEMO_SCRIPT.md` — 5-act step-by-step live demo runbook
  - `OBSERVABILITY_DATA_MODEL.md` — 4-layer trace storage schema (`State`, LangSmith, Snowflake `AGENT_OBSERVABILITY_LOGS`, TruLens `default.sqlite`)
- **GitNexus (`1.6.12`):** Indexed at an older commit (re-run `node .gitnexus/run.cjs analyze --index-only`) (`199` nodes, `303` edges, `8` clusters, `17` flows). Follow `AGENTS.md` rules (`gitnexus impact` before editing symbols; `gitnexus detect-changes` before committing; re-index with `gitnexus analyze` after recent doc edits).

---

## 5. Immediate Next Actions for Incoming Agent

0. **Commit rewritten (2026-11-17).** The old local commit `3abb20d` had the Snowflake account id in four files. It is now `0d17816` with the id replaced by `<account-id>`. The old commit stays only in the local reflog (a push cannot send it). A scan of tracked and untracked files finds no account id or user name.
1. **Decide what to commit from the working tree** (`git status --short` currently has uncommitted `DEMO_SCRIPT.md`, `OBSERVABILITY_DATA_MODEL.md`, `HANDOFF.md`, plus GitNexus-generated `AGENTS.md`, `CLAUDE.md`, `.claude/`).
2. **Run the offline tests:** `.venv/bin/python -m unittest discover -s tests -v` (16 tests, no login). Re-run `gitnexus analyze` after big edits.
3. **Optional Next Refactors / Features:**
   - Unify the session code: `data_generation.py` and the notebook still have their own copies (`snowflake_client._create_snowflake_session()` is the reference).
   - If the user upgrades the Snowflake trial account (adds billing in Snowsight), finish `sql/02_setup_cortex_search.sql` & `sql/04_setup_udfs.sql`, align `sql/05_setup_cortex_agents.sql`, and set `SNOWFLAKE_USE_CORTEX_AGENTS=true`.

---

## 6. Audit Fixes Applied 2026-11-16

**Verified live on Snowflake (tracing off):**
- `studio_app.py`: urgent-ticket count uses `high`+`urgent` (data has no `critical`; API tickets: 132 high or urgent, was 113). A new question in the same thread now re-plans (it used to re-use the old plan and answer). The planner matches word starts, not text inside words (all 4 `EVAL_EXAMPLES` routes unchanged). Generated SQL must be `SELECT`/`WITH`. `SNOWFLAKE_AUTHENTICATOR` now passes the password or PAT (`PROGRAMMATIC_ACCESS_TOKEN` and `snowflake` both log in). Unused `ChatSnowflake` supervisor model removed.
- Snowflake, applied: `AGENT_OBSERVABILITY_LOGS` (`RESULT_LIMIT 10000`), both semantic views (comment fix only), all three agents (function tools). `sql/04` cannot be applied on the trial (`399258`); its date and priority logic was tested with a temporary stand-in for `AI_COMPLETE`.
- `sql/04` date windows count back from the newest data row (the old window matched 0 rows).

**Verified live on LangSmith (2026-11-17, with the owner's go-ahead):**
- Feedback now lands on the root run. A fresh 2-question thread on a new dev server (port 2025) put `execution_success`, `non_empty_results`, `zero_errors` and `step_count` on each turn's root run (step_count 1 for the ticket question, 2 for the churn question). Question 2 made its own plan (no stale plan).
- `evaluate_observability.py` ran to exit 0. LangSmith experiment `snowlanggraph-trial-d27e7ac9`: all 4 examples scored 1.0 on `route_match` and `sql_executed_with_rows`. TruLens recorded 1 record. `AGENT_OBSERVABILITY_LOGS` returned 10 rows with trace ids.
- Side effect found there: the key `zero_errors` appeared twice (evaluator plus online feedback). Fixed after: the evaluator key is now `eval_zero_errors` (needs a re-run of the evaluator to show).

**NOT verified (do not claim as tested):**
- The Cortex Analyst HTTP error-body path never fired (no error occurred).
- `data_generation.py`: two edits (schema-ownership grant wrapped in try/except with `COPY CURRENT GRANTS`; `0` values no longer turned into `NULL`). The script was not run (it would reload the tables).
- Managed mode (`SNOWFLAKE_USE_CORTEX_AGENTS=true`) and the `type: "function"` tool spec in `sql/05`.
- The old dev server on port 2024 (started 10:19AM) has a dead terminal. Every run on it fails with `OSError: [Errno 5]` at the first `print()`. It is not running now. Start it again (see section 1).

**Rollback:** old repo versions are in git (`git show HEAD:sql/05_setup_cortex_agents.sql`, same for `sql/03`, `sql/06`). The previous live Snowflake definitions (view, semantic views, agents) are saved in `.snowflake_backup_20261116.json` (gitignored).

## 7. Parked List Cleared 2026-11-17

Done and tested: lazy session (`get_session()`, import needs no login), `studio_app.py` split into `supervisor.py` / `snowflake_client.py` / `observability.py`, QUERY_TAG sent per statement (two-thread live test: each query kept its own tag; no lock needed), `|` escaped in tables, `print()` replaced by `logging` (a broken-stdout test passes), 16 offline unit tests, intent and fallback tags on the `supervisor` span, evaluator key renamed `eval_zero_errors`, Act 3 wording fixed in the demo docs, the usage-data limit written in the docs (since fixed: see section 8).

Live checks after the split: `.venv/bin/python studio_app.py` exit 0 (same numbers: 502 / 461 / 132, 1,452 / 110); fresh `langgraph dev` two-question thread OK (each question re-planned, feedback on the root run); `evaluate_observability.py` exit 0.

Still open: reloading the data so usage events cover all customers (needs the owner's OK; it overwrites tables and changes every number in the demo docs). `GRAPH_TRACE_ANALYSIS.md` and `graphify-out/` describe the code before the split and are stale.

**Known leftovers from this session:**
- Five test traces named `snowflake_sql_execute` (input `SELECT 1`, agent `A`) sit in the LangSmith project `default`. An early version of the unit tests sent them. The tests now force tracing off (`test_tracing_is_off`). Delete the five runs in the LangSmith UI if you want a clean list.
- The split renamed 7 helpers (dropped the leading underscore): `get_trace_ids`, `annotate_run_tree`, `emit_langsmith_feedback`, `execute_snowflake_sql`, `run_cortex_analyst`, `run_content_sql_search`, `format_rows_markdown`. No old name is left outside `graphify-out/` and `GRAPH_TRACE_ANALYSIS.md`.
- A lock bug in managed mode (the lock was taken twice) was found in review and fixed (`RLock`). A test covers it. Managed mode itself is still untested live.

## 8. Usage backfill (2026-11-17)

`USAGE_EVENTS` had rows for only the first 2,000 customers (101,711 rows). Now it has `508,637` rows for `9,990` customers. Command: `.venv/bin/python data_generation.py --backfill-usage`. It only inserts, and it stops if the table is not the original load. The seed is fixed, so a full reload (`python data_generation.py`) gives the same rows. Build the rows before connecting, because opening the connection uses the random generator. Live smoke check after the backfill gives the same numbers: 502 / 461 / 132, 1,452 / 110 / $589,348.85. `data_generation.py` and the notebook now use the shared `get_session()`.

## 9. Bigger eval set (2026-11-17)

`evaluate_observability.py` now has 12 questions (was 4). Experiment `snowlanggraph-trial-09f9954a`: 12 runs. `route_match`, `eval_zero_errors`, `execution_success` and `zero_errors` pass 12/12. `sql_executed_with_rows` and `non_empty_results` pass 11/12. The one miss is the CLV question (see `PARKED.md`). The demo docs still point to the older 4-question experiment `c05502ee`. That is fine for the demo.

## 10. CLV metric (2026-11-17)

Added `CUSTOMER_LIFETIME_VALUE` (realized CLV of churned customers = final monthly revenue x days since signup / 30) to both semantic views in `sql/03_setup_semantic_views.sql`, and applied it live (`CREATE OR REPLACE`). Both views return the same values (technology about $5,034, retail about $4,878). The CLV question now returns rows through the graph. I also added `AVG_LIFETIME` to the research view (it was only in the analyst view). Smoke check numbers did not change. Act 4 of `DEMO_SCRIPT.md` now tells the "why a Semantic View" story. Average lifetime in Act 4 is about 390 to 450 days, not 1,271. The older experiment `09f9954a` still shows the CLV miss, which is the "before" half of the story.

## 11. Known bad behavior kept on purpose (2026-11-17)

`run_content_sql_search` ORs keywords and matches "urgent" in ticket text. Question "How many urgent API support tickets are there?" gives 729 tickets (642 customers, 186 high/urgent) and includes a password-reset ticket. It is demo Act 4B ("the trace caught bad behavior"). Do not fix it before the demo. Fix after: AND logic or the priority column, plus a relevance evaluator. Example run: `01a1e879-bea7-7405-b387-d60a92d65449` in the LangSmith `default` project.

