"""
LangGraph Studio entry point: `langgraph.json` loads `./studio_app.py:app` (graph `supervisor_workflow`).

The code lives in three modules:
- `observability.py`     progress logging, LangSmith run annotation and feedback
- `snowflake_client.py`  lazy Snowpark session, Cortex Analyst, ticket search, SQL with per-statement QUERY_TAG
- `supervisor.py`        graph state, deterministic planner, agent nodes, synthesis, compiled `app`

Hub-and-spoke: START -> supervisor (planning) -> agent(s) -> supervisor (routing) -> ... -> supervisor (synthesis) -> END

Set SNOWFLAKE_USE_CORTEX_AGENTS=true in .env on an account with full Cortex AI enabled.
Only the worker agents change in that mode. Planning and synthesis stay deterministic Python.
"""

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage  # noqa: E402

from snowflake_client import get_session  # noqa: E402,F401  (used by evaluate_observability.py)
from supervisor import app  # noqa: E402

__all__ = ["app", "get_session"]


if __name__ == "__main__":
    test_queries = [
        "What industries have the highest total MRR and average customer lifetime?",
        "Find customers complaining about API issues and analyze their churn rate and monthly revenue",
    ]
    for test_query in test_queries:
        print(f"\n{'='*60}\nTesting: {test_query}\n{'='*60}\n")
        result = app.invoke({"messages": [HumanMessage(content=test_query)]})
        print(result["messages"][-1].content)
