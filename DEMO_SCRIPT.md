# Live Demo Script: Multi-Agent Customer Intelligence with Snowflake & LangGraph

**Total Runtime:** ~8 minutes (can be trimmed to 4 minutes by skipping Act 1's Knowledge Graph visual and running only Act 3 + Act 4)

---

## Pre-Flight Checklist (2 Minutes Before Demo)

1. **Verify the LangGraph dev server is running** (`http://127.0.0.1:2024`):
   ```bash
   curl -s http://127.0.0.1:2024/ok
   # Expected: {"ok":true}
   ```
   If it's not running, start it from `/Users/yourbabes/dev/SnowLearn/SnowLangGraph`:
   ```bash
   .venv/bin/langgraph dev --allow-blocking --no-browser
   ```

2. **Open 4 browser tabs side-by-side**:
   - **Tab 1 — LangSmith Studio**: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024` (select graph `supervisor_workflow`)
   - **Tab 2 — LangSmith Experiments**: `https://smith.langchain.com/o/4c3b325e-7b1a-496e-9bb2-de5ce01d7097/datasets/0e556ec0-f5fc-4fb2-a1fc-725af7df428c/compare?selectedSessions=4e0d49d1-0613-4597-8e90-14495be4dd67`
   - **Tab 3 — Snowsight SQL Worksheet** (logged into your Snowflake account as `ACCOUNTADMIN`, warehouse `SNOWFLAKE_LEARNING_WH`, schema `CUSTOMER_INTELLIGENCE_DB.PUBLIC`)

3. **Pre-stage this SQL snippet in Tab 3 (Snowsight)**:
   ```sql
   SELECT
       QUERY_ID,
       LANGSMITH_TRACE_ID,
       AGENT_NAME,
       STEP_NUMBER,
       TOOL_NAME,
       EXECUTION_STATUS,
       ELAPSED_MS,
       ROWS_PRODUCED,
       LEFT(QUERY_TEXT, 120) AS SQL_PREVIEW
   FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
   LIMIT 20;
   ```

---

## Act 1: The Problem & Architecture (~1 min)

**Screen:** Tab 1 (LangSmith Studio graph canvas) → briefly switch to Tab 4 (`graph.html`) if showing codebase structure.

**Say:**
> "Business leaders don't ask single-table questions—they ask cross-domain questions like *'Who is complaining about our API, and how much revenue is at risk?'* That spans unstructured support tickets, behavioral churn metrics, and financial exposure.
>
> What you're looking at in **LangSmith Studio** is a hub-and-spoke `LangGraph` `StateGraph` (`supervisor_workflow` in `studio_app.py`):
> - A central **`supervisor`** node that builds an execution plan once, routes step-by-step, and synthesizes the final brief.
> - Three specialists: **`CONTENT_AGENT`** (support tickets), **`DATA_ANALYST_AGENT`** (usage & churn), and **`RESEARCH_AGENT`** (MRR, customer lifetime, and industry trends).
>
> All three share a single typed `State` contract—so there are no repeated LLM routing loops—and talk to live data in Snowflake `CUSTOMER_INTELLIGENCE_DB.PUBLIC`: `10,000` customers, `101,711` usage events, `8,954` support tickets, and `703` churn events, modeled behind two governed **Snowflake Semantic Views** (`CUSTOMER_BEHAVIOR_ANALYST` and `STRATEGIC_RESEARCH_ANALYST`)."

---

## Act 2: Single-Agent Strategic Query (~1.5 min)

**Screen:** Tab 1 (LangSmith Studio — new thread).

**Action:** Paste and submit:
```text
What industries have the highest total MRR and average customer lifetime?
```

**Watch & Point Out:**
1. On the graph canvas, `supervisor` routes directly to **`RESEARCH_AGENT`** (1 step only—skips `CONTENT_AGENT` and `DATA_ANALYST_AGENT`), then back to `supervisor`, then `END`.
2. Scroll the final output to show:
   - **Cortex Analyst's interpretation** and **generated `SEMANTIC_VIEW` / SQL** against `STRATEGIC_RESEARCH_ANALYST`.
   - **Live Snowflake table** (`healthcare` at `$423,966.55` MRR, `finance` at `$405,595.52`, `education` at `$397,174.61`, ~`1,271+` days avg lifetime).
   - **Observability Telemetry footer** at the bottom (`LangSmith Trace ID` + Snowflake `sfqid`).

**Say:**
> "Notice two things here. First, the supervisor didn't waste calls on irrelevant agents—it planned a 1-step route straight to `RESEARCH_AGENT`. Second, we're running on a Snowflake trial account where `AI_COMPLETE` and `Cortex Search` are gated, yet **Snowflake Semantic Views and the Cortex Analyst REST API work natively**. Cortex Analyst translated English into governed SQL and executed it live on `SNOWFLAKE_LEARNING_WH`."

---

## Act 3: Multi-Agent Chained Query (~2 min)

**Screen:** Tab 1 (LangSmith Studio — new thread).

**Action:** Paste and submit:
```text
Find customers complaining about API issues and analyze their churn rate and monthly revenue
```

**Watch & Point Out (as each node lights up):**
1. **`CONTENT_AGENT`**: Queries `SUPPORT_TICKETS` via parameterized SQL → isolates `502` API-related tickets across `461` customers (`264` `technical_issue`, `238` `integration_help`, average CSAT `3.03`, `132` high or urgent tickets).
2. **`DATA_ANALYST_AGENT`**: Calls Cortex Analyst (`CUSTOMER_BEHAVIOR_ANALYST`) → joins ticketed categories (`technical_issue`, `integration_help`) with `CUSTOMERS` → returns `1,452` customers, `110` churned (`7.58%` churn rate).
3. **`RESEARCH_AGENT`**: Calls Cortex Analyst (`STRATEGIC_RESEARCH_ANALYST`) → quantifies financial exposure for the same `1,452` customers: `$405.89` avg MRR and `$589,348.85` total monthly revenue. Say it as "customers with technical or integration tickets". The analyst works on ticket categories, not on the `461` customers who mention the API.

**Say:**
> "Now watch a cross-domain question. The supervisor builds a 3-step pipeline: `CONTENT_AGENT` isolates the complaint cohort in `SUPPORT_TICKETS`, `DATA_ANALYST_AGENT` joins those categories with customer status via `CUSTOMER_BEHAVIOR_ANALYST` to surface a `7.58%` churn rate, and `RESEARCH_AGENT` quantifies `$589,348.85` in monthly recurring revenue exposure—synthesized into one executive brief with every query ID attached."

---

## Act 4: End-to-End Observability — LangSmith ↔ Snowflake Correlation (~2 min)

**Screen:** Tab 1 → click the **View in LangSmith** / trace link on the run you just executed, then switch to Tab 3 (Snowsight).

**Actions in LangSmith Trace View:**
1. Expand the waterfall: `supervisor` → `CONTENT_AGENT` / `DATA_ANALYST_AGENT` / `RESEARCH_AGENT` → **`cortex_analyst_text_to_sql`** (`llm`) & **`snowflake_support_tickets_search`** (`retriever`) → **`snowflake_sql_execute`** (`tool`).
2. Click a `snowflake_sql_execute` span and point out its metadata:
   - `snowflake_query_id` (`sfqid`)
   - `snowflake_query_tag` (JSON with `app`, `trace_id`, `agent`, `step`, `tool`)
   - `elapsed_ms`, `row_count`, `warehouse`
3. Click the **Feedback** tab on the root trace to show the automated online checks:
   - `execution_success: 1.0`
   - `non_empty_results: 1.0`
   - `zero_errors: 1.0`
   - `step_count: 3.0`
4. Copy the **`LangSmith Trace ID`** from the telemetry footer (or trace header).

**Actions in Tab 3 (Snowsight):**
Run your staged query (or filter `WHERE LANGSMITH_TRACE_ID = '<paste_trace_id>'`):
```sql
SELECT
    QUERY_ID,
    LANGSMITH_TRACE_ID,
    AGENT_NAME,
    STEP_NUMBER,
    TOOL_NAME,
    EXECUTION_STATUS,
    ELAPSED_MS,
    ROWS_PRODUCED,
    LEFT(QUERY_TEXT, 120) AS SQL_PREVIEW
FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
WHERE LANGSMITH_TRACE_ID = '<paste_trace_id>'
ORDER BY STEP_NUMBER;
```

**Say:**
> "Most agent demos stop at LangSmith node timings, leaving database execution as a black box. Here we have **two-way correlation**: every child span in LangSmith carries the exact Snowflake `sfqid` and `request_id`, and every Snowflake query carries a structured `QUERY_TAG` with the `LANGSMITH_TRACE_ID`. In Snowsight's `AGENT_OBSERVABILITY_LOGS` view—built over `INFORMATION_SCHEMA.QUERY_HISTORY`—we can slice latency, bytes scanned, and rows produced per agent step, or click straight into Snowflake's Query Profile."

---

## Act 5: Offline Evals & Production Upgrade Path (~1.5 min)

**Screen:** Tab 2 (LangSmith Experiments) + terminal (optional live run).

**Optional Live Command (takes ~35s):**
```bash
.venv/bin/python evaluate_observability.py
```

**Say (showing Tab 2 `snowlanggraph-trial-2f4a712d`):**
> "Evals sit at two points in this system:
> 1. **Online feedback** watches every live request (`execution_success`, `non_empty_results`, `zero_errors`).
> 2. **Offline regression evals** in `evaluate_observability.py` run `langsmith.evaluate()` against a golden dataset of single-agent and 3-agent prompts—checking `route_match`, `sql_executed_with_rows`, and `zero_errors`—plus `TruGraph` (`trulens-apps-langgraph`) OTEL recording.
>
> Whenever we modify a Semantic View or planner rule, we re-run the suite to compare experiments side-by-side before shipping. And when moving from this trial org to a full Snowflake account, after you run `sql/02` and `sql/04` and set `SNOWFLAKE_USE_CORTEX_AGENTS=true`, the same graph uses Snowflake-managed Cortex Agents and Cortex Search vector indexes, and TruLens's Cortex-backed Agent GPA judges can run (untested on this trial org) (`Plan Quality`, `Plan Adherence`, `Execution Efficiency`, `Logical Consistency`) on the exact same LangGraph graph."

---

## Quick Troubleshooting Card (If Anything Hiccups Live)

| Symptom | Fix |
| --- | --- |
| LangSmith Studio won't connect | Verify server with `curl -s http://127.0.0.1:2024/ok`; restart via `.venv/bin/langgraph dev --allow-blocking --no-browser` |
| Cold warehouse latency on first query | Expected `1–3s` as `SNOWFLAKE_LEARNING_WH` auto-resumes; run Act 2 once during pre-flight to warm it |
| `AGENT_OBSERVABILITY_LOGS` shows old rows | Remember to filter by the latest `LANGSMITH_TRACE_ID` from the Studio telemetry footer (top `LIMIT 20` is newest-first) |
| Need to reset / commit clean state | `.langgraph_api/` and `default.sqlite` are gitignored |
