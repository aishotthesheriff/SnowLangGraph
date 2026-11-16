# Multi-Agent Customer Intelligence with Snowflake & LangGraph

**One-Page Executive Summary & Demo Talk Track**

---

## 1. Executive Summary

We built and deployed a live **multi-agent customer intelligence system** orchestrating **LangGraph** (`studio_app.py`) over **Snowflake** (`<account-id>`, `AWS_US_EAST_2`, `SNOWFLAKE_LEARNING_WH`), running locally via `langgraph dev` (`http://127.0.0.1:2024`) with **LangSmith Studio** visual tracing and debugging.

### Live Snowflake Assets (`CUSTOMER_INTELLIGENCE_DB.PUBLIC`)
- **4 Core Tables Loaded**:
  - `CUSTOMERS` (`10,000` rows)
  - `USAGE_EVENTS` (`101,711` rows)
  - `SUPPORT_TICKETS` (`8,954` rows)
  - `CHURN_EVENTS` (`703` rows)
- **2 Semantic Views** (`sql/03_setup_semantic_views.sql`):
  - `CUSTOMER_BEHAVIOR_ANALYST` (usage, engagement, satisfaction, churn)
  - `STRATEGIC_RESEARCH_ANALYST` (MRR, customer lifetime, industry segmentation, lost revenue)
- **3 Cortex Agent Definitions** (`SNOWFLAKE_INTELLIGENCE.AGENTS` via `sql/05_setup_cortex_agents.sql`):
  - `CONTENT_AGENT`, `DATA_ANALYST_AGENT`, `RESEARCH_AGENT`

### Multi-Agent Architecture (`supervisor_workflow`)

```mermaid
flowchart LR
    START([START]) --> SUP[supervisor: Plan / Route / Synthesize]
    SUP -->|Step routing| CA[CONTENT_AGENT: Support Tickets SQL]
    SUP -->|Step routing| DA[DATA_ANALYST_AGENT: Cortex Analyst - Behavior]
    SUP -->|Step routing| RA[RESEARCH_AGENT: Cortex Analyst - Strategy]
    CA --> SUP
    DA --> SUP
    RA --> SUP
    SUP --> END([END])
```

- **`DATA_ANALYST_AGENT` & `RESEARCH_AGENT`**: Call Snowflake's live **Cortex Analyst REST API** (`/api/v2/cortex/analyst/message`) against `CUSTOMER_BEHAVIOR_ANALYST` and `STRATEGIC_RESEARCH_ANALYST`, then execute the generated `SEMANTIC_VIEW(...)` SQL directly on Snowflake.
- **`CONTENT_AGENT`**: Executes parameterized Snowflake SQL against `SUPPORT_TICKETS` for keyword/customer matching, CSAT averages, priority counts, and category breakdowns.
- **`supervisor_node`**: Creates a structured multi-step plan, routes dynamically via LangGraph `Command(goto=...)`, propagates context across steps, and synthesizes a final executive markdown brief.

### Structural & Trial-Org Insights (from Code + Knowledge Graph Trace)
- **Trial-Org Adaptation**: Unpaid Snowflake trial accounts restrict `AI_COMPLETE`, `AI_EMBED`/`EMBED_TEXT_*` (Cortex Search), Cortex Inference (`403`), and the Cortex Agent `:run` endpoint (`399504`), while new accounts also block legacy `SNOWFLAKE.CORTEX.COMPLETE` (`399270`). Because **Semantic Views and Cortex Analyst REST text-to-SQL run natively on trial accounts**, `studio_app.py` pairs live Cortex Analyst + parameterized SQL today—while preserving `SNOWFLAKE_USE_CORTEX_AGENTS=true` in `.env` as a one-line upgrade path for full Cortex accounts.
- **Core Graph Bridges (`State` & `get_latest_human_message()`)**: Knowledge-graph analysis (`130` nodes, `187` edges, `12` communities in `graphify-out/`) confirmed `State` (`12` edges, top God Node) and `get_latest_human_message()` (`5` edges) are the shared contract bridging supervisor planning/routing (`supervisor_node()`) to all worker nodes (`_execute_agent_step()`).
- **Dual Observability Stack (`LangSmith` + `Snowflake` + `TruLens`)**:
  - **LangSmith Nested Waterfall**: `@traceable` spans wrap `cortex_analyst_text_to_sql` (`llm`), `snowflake_support_tickets_search` (`retriever`), and `snowflake_sql_execute` (`tool`), logging `sfqid`, Cortex Analyst `request_id`, row counts, SQL latency, and deterministic feedback scores (`execution_success`, `non_empty_results`, `zero_errors`, `step_count`).
  - **Snowflake Telemetry View (`AGENT_OBSERVABILITY_LOGS`)**: Every SQL query emits a structured JSON `QUERY_TAG` (`app`, `trace_id`, `agent`, `step`, `tool`), queried live via `CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS` (`sql/06_setup_observability_view.sql`) for two-way trace ↔ Snowsight correlation.
  - **LangSmith Evaluation & TruLens OTEL**: `evaluate_observability.py` runs `langsmith.evaluate(...)` across a 4-example dataset (`route_match`, `sql_executed_with_rows`, `zero_errors`) plus `TruGraph` (`trulens-apps-langgraph`) OTEL recording (enabled by pinning `sqlalchemy<2.1`).
- **Modularization & Bypass Findings**: `studio_app.py` mixes session/Snowflake client logic with supervisor orchestration (cohesion `0.0954`), and both `data_generation.py` and the notebook call `create_session_from_env()` directly rather than sharing `_create_snowflake_session()`. Splitting `snowflake_client.py` from `supervisor.py` is the cleanest next refactor. (Full trace in `GRAPH_TRACE_ANALYSIS.md`.)

---

## 2. Demo Talk Track (2–3 Minutes)

### Opening — The Problem (20 sec)
> "Executives rarely ask single-table questions—they ask cross-domain questions like *'Who is complaining about our API, and how much revenue is at risk?'* Answering that requires unstructured support ticket analysis, behavioral churn metrics, and strategic revenue context. Here, we use **LangGraph** as an orchestrating supervisor over **Snowflake** data and **Cortex Analyst Semantic Views**, with **LangSmith Studio** providing step-by-step trace visibility."

### Step 1 — Architecture, State Contract & Semantic Governance (30 sec)
*(Show the graph topology in LangSmith Studio — optionally flash `graphify-out/graph.html`)*
> "In LangSmith Studio you can see our hub-and-spoke `StateGraph`: a central `supervisor` node coordinating three domain specialists—`CONTENT_AGENT` for support tickets, `DATA_ANALYST_AGENT` for usage and churn behavior, and `RESEARCH_AGENT` for MRR and industry CLV. Every hop shares a typed `State` contract—our most connected abstraction in the codebase—storing an immutable plan, step index, and aggregated outputs so there are no wasted LLM routing calls. In Snowflake `CUSTOMER_INTELLIGENCE_DB.PUBLIC`, our tables sit behind governed `SEMANTIC VIEW` definitions so Cortex Analyst generates accurate, repeatable SQL without guessing joins or KPI definitions."

### Step 2 — Live Run #1: Single-Agent Strategic Query (40 sec)
*(Prompt to run in Studio: `"What industries have the highest total MRR and average customer lifetime?"`)*
> "When we ask a strategic revenue question, the `supervisor` builds an execution plan and routes straight to `RESEARCH_AGENT`—no unnecessary hops. `RESEARCH_AGENT` calls Snowflake's Cortex Analyst REST API against `STRATEGIC_RESEARCH_ANALYST`, generates the `SEMANTIC_VIEW` SQL, and executes it live on `SNOWFLAKE_LEARNING_WH`. We immediately see Healthcare ($423,966.55 MRR), Finance ($405,595.52), and Education ($397,174.61) leading, with ~1,271+ days average customer lifetime."

### Step 3 — Live Run #2: Multi-Agent Chained Query (60 sec)
*(Prompt to run in Studio: `"Find customers complaining about API issues and analyze their churn rate and monthly revenue"`)*
> "Now watch a cross-domain question. The `supervisor` plans a 3-step pipeline:
> 1. **`CONTENT_AGENT`** searches `SUPPORT_TICKETS` and isolates `502` API-related tickets across `461` customers (`264` `technical_issue`, `238` `integration_help`, average CSAT `3.03`, `113` urgent).
> 2. **`DATA_ANALYST_AGENT`** queries `CUSTOMER_BEHAVIOR_ANALYST` via Cortex Analyst, joining ticketed customers with `CUSTOMERS` to find `1,452` customers in those categories, `110` churned—a `7.58%` churn rate.
> 3. **`RESEARCH_AGENT`** queries `STRATEGIC_RESEARCH_ANALYST` to quantify financial exposure: `$405.89` average MRR and `$589,348.85` total monthly revenue tied to API-affected accounts—all traced end-to-end in LangSmith."

### Closing — Production & Observability Path (25 sec)
> "Everything runs live against our Snowflake trial org today via Cortex Analyst and Semantic Views, with **LangSmith Studio** capturing every state transition in real time. When promoted to a production Snowflake account, flipping `SNOWFLAKE_USE_CORTEX_AGENTS=true` swaps in Snowflake-managed Cortex Agents, Cortex Search vector indexes, and AI SQL UDFs—and unlocks **TruLens** (`trulens-apps-langgraph`) alongside LangSmith for automated Cortex evaluation, with a clean path to modularize `studio_app.py` into shared Snowflake client and supervisor modules."

---

## 3. Quick Reference Commands & Artifacts

```bash
# Run CLI verification tests
.venv/bin/python studio_app.py

# Run LangSmith eval + TruLens recording + Snowflake audit check
.venv/bin/python evaluate_observability.py

# Start LangGraph dev server + Studio API
.venv/bin/langgraph dev --allow-blocking --no-browser
```

- **Local API**: `http://127.0.0.1:2024` (`http://127.0.0.1:2024/docs`)
- **LangSmith Studio**: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`
- **Snowflake Audit View**: `SELECT * FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS LIMIT 20;`
- **Knowledge Graph & Trace**: `graphify-out/graph.html`, `graphify-out/GRAPH_REPORT.md`, `GRAPH_TRACE_ANALYSIS.md`
