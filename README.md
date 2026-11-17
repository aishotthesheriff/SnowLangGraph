# Build and Evaluate Agents with Snowflake and LangGraph

A multi-agent AI system built with **LangGraph** and **Snowflake Cortex** for customer analytics, churn analysis, and business intelligence.

Try it yourself by following the [developer guide](https://www.snowflake.com/en/developers/guides/build-and-evaluate-agents-with-langgraph-and-snowflake/).

## Quick start

1. Install: `uv venv && uv pip install -r requirements.txt`
2. Copy `.env.template` to `.env` and fill in your Snowflake values.
3. Run the SQL files in `sql/` in order (`01` to `06`). On an unpaid trial account `02` and `04` fail (`399258`). The app works without them.
4. Smoke test: `.venv/bin/python studio_app.py`
5. Studio: `.venv/bin/langgraph dev --allow-blocking --no-browser`
6. Evals: `.venv/bin/python evaluate_observability.py` (needs `LANGSMITH_API_KEY`; it publishes an experiment to LangSmith)

## Project layout

- `studio_app.py`: Studio entry point (`langgraph.json` loads `studio_app.py:app`).
- `supervisor.py`: graph state, planner, agent nodes, synthesis.
- `snowflake_client.py`: lazy Snowflake session, Cortex Analyst, ticket search, SQL with per-statement QUERY_TAG.
- `observability.py`: logging, LangSmith annotation and feedback.
- `evaluate_observability.py`: LangSmith evals, TruLens recording, audit-view check.
- `tests/`: offline tests, no login needed. Run `.venv/bin/python -m unittest discover -s tests -v`.

## Two modes

- **Trial mode** (`SNOWFLAKE_USE_CORTEX_AGENTS=false`, default): uses the Cortex Analyst REST API and plain SQL.
- **Managed mode** (`true`): calls the Cortex Agents from `sql/05`. Needs a paid account.

See `SPEC.md` for the design and `OBSERVABILITY_DATA_MODEL.md` for the trace model.

## License

Apache 2.0 - See [LICENSE](LICENSE) file.
