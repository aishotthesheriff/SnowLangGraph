# Live Demo Script: Multi-Agent Customer Intelligence with Snowflake & LangGraph

**Total Runtime:** ~8 minutes. For a 4-minute cut, run only Act 1 (cold open) and Act 3 (split screen).

**Story shape:** show the result first, explain it second, prove it third.

| Act | What the audience sees | Time |
| --- | --- | --- |
| 1 | Cold open: one hard question, three agents light up live | ~2 min |
| 2 | "What you just saw": the architecture, on the same canvas | ~1 min |
| 3 | Split screen: LangSmith waterfall next to a Snowflake chart of the same run | ~2 min |
| 4 | Contrast: a simple question takes one hop, not three | ~1 min |
| 5 | Evals and the production upgrade path | ~1.5 min |

---

## Pre-Flight Checklist (10 Minutes Before Demo)

1. **Check the Snowflake login still works.** The login uses a Programmatic Access Token (PAT). Run:
   ```bash
   .venv/bin/python studio_app.py
   # Expected: two answers printed, exit code 0
   ```
   If you see `Network policy is required`, the token can no longer log in from this network. Fix it in Snowsight before the demo (see the troubleshooting card).

2. **Start the LangGraph dev server.** It is not left running. Start it from `/Users/yourbabes/dev/SnowLearn/SnowLangGraph` in a terminal that stays open for the whole demo:
   ```bash
   .venv/bin/langgraph dev --allow-blocking --no-browser
   ```
   Then check it:
   ```bash
   curl -s http://127.0.0.1:2024/ok
   # Expected: {"ok":true}
   ```

3. **Open 3 browser tabs**:
   - **Tab 1 — LangSmith Studio**: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024` (select graph `supervisor_workflow`)
   - **Tab 2 — LangSmith Experiments** (latest run `snowlanggraph-trial-c05502ee`): `https://smith.langchain.com/o/4c3b325e-7b1a-496e-9bb2-de5ce01d7097/datasets/0e556ec0-f5fc-4fb2-a1fc-725af7df428c/compare?selectedSessions=9b29a555-a29e-4819-838a-43832c62973f`
   - **Tab 3 — Snowsight SQL Worksheet** (role `ACCOUNTADMIN`, warehouse `SNOWFLAKE_LEARNING_WH`, schema `CUSTOMER_INTELLIGENCE_DB.PUBLIC`)

4. **Set up the split screen for Act 3.** Drag Tab 3 (Snowsight) into its own window. Snap Tab 1 to the left half of the screen and Snowsight to the right half. Zoom both to about 125% so the back of the room can read them.

5. **Pre-stage the chart query in Tab 3 (Snowsight)**:
   ```sql
   -- One bar per agent step for one LangSmith trace
   SELECT
       TO_VARCHAR(STEP_NUMBER) || ' - ' || AGENT_NAME AS STEP_LABEL,
       COUNT(*)            AS QUERIES,
       SUM(ELAPSED_MS)     AS TOTAL_MS,
       SUM(ROWS_PRODUCED)  AS ROWS_OUT
   FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
   WHERE LANGSMITH_TRACE_ID = '<paste_trace_id>'
   GROUP BY STEP_NUMBER, AGENT_NAME
   ORDER BY STEP_NUMBER;
   ```

6. **Rehearse the chart once.** Run the cold-open question in Studio, paste its trace id into the query, run it, then click **Chart** in the results pane. Set chart type **Bar**, X-axis `STEP_LABEL`, value `TOTAL_MS` or `ROWS_OUT` (pick whichever draws readable bars). Leave that worksheet open so the chart settings stay in place. Check that the bars draw again after a second run. This run also warms the warehouse.

---

## Act 1: Cold Open — The Hard Question, Live (~2 min)

**Screen:** Tab 1 (LangSmith Studio — new thread), full width. No slides. No architecture talk yet.

**Say (one sentence, then paste):**
> "Leaders don't ask single-table questions. They ask this."

**Action:** Paste and submit:
```text
Find customers complaining about API issues and analyze their churn rate and monthly revenue
```

**Narrate while each node lights up on the canvas (do not talk over the first one — let them watch it):**
1. **`CONTENT_AGENT`** lights up: "First it reads the support tickets." → `502` API-related tickets across `461` customers (`264` `technical_issue`, `238` `integration_help`, average CSAT `3.03`, `132` high or urgent).
2. **`DATA_ANALYST_AGENT`** lights up: "Then it asks Snowflake Cortex Analyst about churn." → `1,452` customers with technical or integration tickets, `110` churned (`7.58%` churn rate).
3. **`RESEARCH_AGENT`** lights up: "Then it puts a dollar figure on it." → `$405.89` average MRR and `$589,348.85` total monthly revenue for the same `1,452` customers.

Say the money figure as "customers with technical or integration tickets". The analysts work on ticket categories, not only on the `461` customers who mention the API.

**Land the open (scroll to the final brief):**
> "Three data domains, three specialists, one executive brief, and the SQL for each step is traceable. Now let me show you what you just watched."

---

## Act 2: "What You Just Saw" — Architecture & the Trade-Off (~1 min)

**Screen:** Tab 1, same canvas, the finished run still highlighted.

**Say (point at the nodes that just ran):**
> "This is a hub-and-spoke LangGraph `StateGraph`. One **`supervisor`** plans once, routes step by step, and writes the final brief. Three specialists do the work: **`CONTENT_AGENT`** on support tickets, **`DATA_ANALYST_AGENT`** on usage and churn, **`RESEARCH_AGENT`** on revenue and lifetime. They share one typed `State` contract, and they read live Snowflake data—`10,000` customers, `8,954` tickets—behind two governed **Semantic Views**.
>
> Here is the design decision I want to call out. The planner is not an LLM. It is deterministic Python: it matches question terms to agents. I chose that on purpose. This is a Snowflake trial account, and it blocks `AI_COMPLETE`. So I put the LLM only where it earns its cost—Cortex Analyst turning English into governed SQL—and kept routing deterministic. The result: the same question takes the same route every time, routing costs zero tokens, and I can test it with an offline unit-test suite and no Snowflake login."

**If asked "what about questions the rules miss?":**
> "Unmatched questions fall back to the data analyst, and the trace is tagged `planner_fallback`, so I can count misses in LangSmith. On a full account, the same graph swaps the workers for Snowflake-managed Cortex Agents with one environment flag. That mode is written but untested on this trial."

---

## Act 3: Same Run, Two Systems — Split Screen (~2 min)

**Screen:** Split. **Left half:** LangSmith trace of the Act 1 run. **Right half:** Snowsight worksheet with the staged chart query.

**Left (LangSmith):**
1. In Studio, click the trace link for the Act 1 run. Expand the waterfall: `supervisor` → three agents → **`cortex_analyst_text_to_sql`** (`llm`) and **`snowflake_support_tickets_search`** (`retriever`) → **`snowflake_sql_execute`** (`tool`).
2. Click one `snowflake_sql_execute` span. Point at `snowflake_query_id` and `snowflake_query_tag` in its metadata.
3. Click the **Feedback** tab on the root run: `execution_success: 1.0`, `non_empty_results: 1.0`, `zero_errors: 1.0`, `step_count: 3.0`.
4. Copy the **trace ID**. Say: "I am copying this ID out of LangSmith."

**Right (Snowsight):**
5. Paste the trace ID into the staged query. Say: "And pasting it into Snowflake." Run it.
6. Click **Chart**. Expect **5 bars** grouped by 3 agents (tested live 2026-11-17): `CONTENT_AGENT` has 3 (`SUPPORT_TICKETS_SUMMARY` ~106 ms, `SUPPORT_TICKETS_BREAKDOWN` ~613 ms, `SUPPORT_TICKETS_SAMPLES` ~31 ms), `DATA_ANALYST_AGENT` has 1 (~44 ms), `RESEARCH_AGENT` has 1 (~253 ms). Timings vary per run, so do not quote them. The Cortex Analyst REST call itself is not tagged. Only the SQL it produces is, and that is what you see.

**Say (gesture left, then right):**
> "Most agent demos stop on the left. You see node timings, and the database is a black box. On the right is Snowflake's own query history for the same run—steps on the left, bars on the right. The link is two-way: every LangSmith SQL span carries the Snowflake query ID, and every SQL statement the agent runs carries a `QUERY_TAG` with the LangSmith trace ID. The tag is sent per statement, so concurrent users on one shared session never mix their tags. Text-to-SQL time lives on the LangSmith side, while a platform team can now check SQL time and rows per step, from inside Snowflake, with no access to LangSmith."

**Optional drill-down (if someone asks to see the rows):**
```sql
SELECT QUERY_ID, AGENT_NAME, STEP_NUMBER, TOOL_NAME, EXECUTION_STATUS,
       ELAPSED_MS, ROWS_PRODUCED, LEFT(QUERY_TEXT, 120) AS SQL_PREVIEW
FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
WHERE LANGSMITH_TRACE_ID = '<paste_trace_id>'
ORDER BY STEP_NUMBER, START_TIME;
```

---

## Act 4: One Hop, and Why a Semantic View (~2 min)

**Screen:** Tab 1 full width again (LangSmith Studio — new thread). Cut the CLV part first if time is short.

**Action 1:** Paste and submit:
```text
What industries have the highest total MRR and average customer lifetime?
```

**Watch & Point Out:**
1. Only **`RESEARCH_AGENT`** lights up. The other two stay dark.
2. The final output shows Cortex Analyst's interpretation, the generated `SEMANTIC_VIEW` SQL, and the live table (`healthcare` at `$423,966.55` MRR, `finance` at `$405,595.52`, `education` at `$397,174.61`; average lifetime about `390` to `450` days for churned customers).

**Say:**
> "Same graph, different question, one hop. The supervisor does not spend calls on agents it does not need."

**Action 2 (the Semantic View story):** Paste and submit:
```text
What is the average customer lifetime value by industry?
```
Point at the interpretation line: `realized CLV = average of final_monthly_revenue * days_since_signup / 30`. The top industry is `technology` at about `$5,000` (`retail` is next at about `$4,900`).

**Say (this is the "why"):**
> "Here is why I put a Semantic View between the agents and the data. Before I fixed it, I ran this exact question and the system did not guess. Cortex Analyst answered: 'customer lifetime value is not defined in the semantic model, and there are several valid ways to calculate it. Which one do you mean?' It wrote no SQL. In my evaluation that showed up as one failed run out of twelve—I did not hide it, I treated it as a finding.
>
> A model that guesses a business formula gives you a confident number nobody can audit. So I defined CLV once, in Snowflake: final monthly revenue times months active, for churned customers. Now both specialist agents use the same definition, the question works, and the answer comes with the formula in plain view. The definition lives in the data platform, under Snowflake's access controls, not in a prompt that someone can quietly edit.
>
> That is the pattern: the LLM translates English into a query, but it can only use metrics, joins and synonyms that the business has defined. Same question, same answer, every run—and it is testable, because my evaluation suite caught this gap before a customer did."

**If asked "is that a good CLV formula?":**
> "It is a realized value for customers who already left, so it understates customers who are still active. I chose the simple, honest definition and wrote that limit in the metric comment. A predictive CLV is a better next step, and it would be a change in one place, in the Semantic View."

---

## Act 4B: The Trace Caught Bad Behavior (~1.5 min, optional)

**Screen:** Tab 1 (Studio, new thread), then the LangSmith trace for that run.

**Action:** Paste and submit:
```text
How many urgent API support tickets are there?
```
Expect `729` matching tickets, `642` customers, `186` "urgent" (verified 2026-11-17 on run `01a1e879-bea7-7405-b387-d60a92d65449`). The sample table includes `TKT_00006033`: "I forgot my password and the reset email isn't coming through. Need access urgently." That is an `account_access` ticket. It has nothing to do with the API.

**In the LangSmith trace:**
1. Open `snowflake_support_tickets_search`, then the three `snowflake_sql_execute` spans. Point at the SQL: the keywords are OR-ed together.
2. Say the keywords out loud: `urgent`, `api`, `support`, `tickets`, `there`. Any ticket that matches one word counts.

**Say:**
> "I want to show you a failure on purpose. The run succeeded: no errors, rows returned, green checks. But look at the answer. It says 729 urgent API tickets, and one of the samples is a password reset. The trace shows why. My ticket search splits the question into five keywords and ORs them. The word 'urgent' matches a password ticket that says 'need access urgently,' and 'there' and 'support' match almost everything. Also, 'urgent' is searched in the ticket text, not in the priority column.
>
> Nothing crashed, so a normal error alert would stay quiet. The trace is what let me see it. And now I get to decide what to do with it: require every keyword (AND), use the priority column for 'urgent,' or hand this question to Cortex Analyst and a Semantic View, which already know what a priority is. Without the trace, I would be shipping 729 as the answer."

**Honest limit (say it if asked):**
> "My current evaluators check routing, SQL ran, rows returned, and zero errors. They all pass on this run. None of them check that the answer is relevant. That is the next evaluator I would add, and this run is the first test case for it."

**Note:** `186` counts tickets with priority `high` or `urgent`, so the "urgent" label is also loose. Do not quote `186` as "urgent only."

---

## Act 5: Offline Evals & Production Upgrade Path (~1.5 min)

**Screen:** Tab 2 (LangSmith Experiments, `snowlanggraph-trial-c05502ee`) + terminal (optional live run).

**Optional Live Command (takes ~35s):**
```bash
.venv/bin/python evaluate_observability.py
```

**Say:**
> "Evals sit at two points in this system:
> 1. **Online feedback** scores every live request (`execution_success`, `non_empty_results`, `zero_errors`, `step_count`). You saw those in the trace.
> 2. **Offline regression evals** in `evaluate_observability.py` run `langsmith.evaluate()` against a golden dataset of single-agent and 3-agent questions. They check `route_match`, `sql_executed_with_rows`, and `eval_zero_errors`, and record the run with TruLens `TruGraph`.
>
> When I change a Semantic View or a planner rule, I re-run the suite and compare experiments side by side before shipping. On a full Snowflake account, after `sql/02` and `sql/04` run and `SNOWFLAKE_USE_CORTEX_AGENTS=true` is set, the same graph uses Snowflake-managed Cortex Agents and Cortex Search, and TruLens's Cortex-backed judges can score plan quality and adherence. I have not been able to test that mode on this trial account, and I say so in the handoff notes."

---

## Quick Troubleshooting Card (If Anything Hiccups Live)

| Symptom | Fix |
| --- | --- |
| `Network policy is required` at Snowflake login | The user has no network policy allowing this IP (or any temporary PAT bypass window expired). In Snowsight or SQL, attach a network policy to the user (`ALTER USER <user> SET NETWORK_POLICY = <policy>`), or for a human user (`TYPE = PERSON`) add/modify a PAT with `MINS_TO_BYPASS_NETWORK_POLICY_REQUIREMENT = <1..1440>` and update `SNOWFLAKE_PASSWORD` in `.env`. Do this before the demo, not during it. |
| LangSmith Studio won't connect | Verify server with `curl -s http://127.0.0.1:2024/ok`; restart via `.venv/bin/langgraph dev --allow-blocking --no-browser` |
| Every run fails with `OSError: [Errno 5]` | The terminal that started the dev server was closed. Restart the server in a terminal that stays open. |
| Cold warehouse latency on first query | Expected `1–3s` as `SNOWFLAKE_LEARNING_WH` auto-resumes; the pre-flight rehearsal warms it |
| Chart query returns no rows | Query history can lag a few seconds. Run it again. If the paste failed, replace the `WHERE` line with `WHERE LANGSMITH_TRACE_ID = (SELECT LANGSMITH_TRACE_ID FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS ORDER BY START_TIME DESC LIMIT 1)` to chart the newest run. |
| Chart shows a table, not bars | Click **Chart**, set type **Bar**, X-axis `STEP_LABEL`, value `TOTAL_MS` or `ROWS_OUT` |
| Need to reset / commit clean state | `.langgraph_api/` and `default.sqlite` are gitignored |
