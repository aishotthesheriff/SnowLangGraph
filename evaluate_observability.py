"""
Observability & Evaluation Suite for SnowLangGraph

Runs three live demonstrations against Snowflake (CUSTOMER_INTELLIGENCE_DB.PUBLIC):
1. LangSmith Dataset & Experiment Evaluation (`langsmith.evaluate`) with deterministic
   evaluators for routing accuracy, SQL execution, non-empty output, and error-free runs.
2. TruLens OTEL Recording (`TruGraph` + `TruSession`) capturing LangGraph execution spans.
3. Snowflake-side query audit verification via `AGENT_OBSERVABILITY_LOGS` showing the
   correlated `LANGSMITH_TRACE_ID` -> `QUERY_ID` (`sfqid`) trail.
"""

import json
import os
import sys
import uuid
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langsmith import Client, evaluate
from trulens.apps.langgraph import TruGraph
from trulens.core import TruSession

from studio_app import app, get_session

load_dotenv()

DATASET_NAME = "SnowLangGraph Customer Intelligence Eval"

EVAL_EXAMPLES = [
    {
        "inputs": {"question": "What industries have the highest total MRR and average customer lifetime?"},
        "outputs": {"expected_agents": ["RESEARCH_AGENT"]},
    },
    {
        "inputs": {"question": "How many customers are in each plan type and what is their churn rate?"},
        "outputs": {"expected_agents": ["DATA_ANALYST_AGENT"]},
    },
    {
        "inputs": {"question": "Show customer feedback and support tickets about billing"},
        "outputs": {"expected_agents": ["CONTENT_AGENT"]},
    },
    {
        "inputs": {"question": "Find customers complaining about API issues and analyze their churn rate and monthly revenue"},
        "outputs": {"expected_agents": ["CONTENT_AGENT", "DATA_ANALYST_AGENT", "RESEARCH_AGENT"]},
    },
]


def _ensure_dataset(client: Client) -> str:
    """Create or reuse the LangSmith evaluation dataset with canonical examples."""
    if client.has_dataset(dataset_name=DATASET_NAME):
        ds = client.read_dataset(dataset_name=DATASET_NAME)
        existing = list(client.list_examples(dataset_id=ds.id))
        current = sorted((json.dumps(e.inputs, sort_keys=True), json.dumps(e.outputs, sort_keys=True)) for e in existing)
        wanted = sorted((json.dumps(e["inputs"], sort_keys=True), json.dumps(e["outputs"], sort_keys=True)) for e in EVAL_EXAMPLES)
        if current == wanted:
            return str(ds.id)
        for ex in existing:
            client.delete_example(ex.id)
    else:
        ds = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="Multi-agent Snowflake Cortex Analyst + SQL evaluation suite",
        )

    client.create_examples(
        inputs=[e["inputs"] for e in EVAL_EXAMPLES],
        outputs=[e["outputs"] for e in EVAL_EXAMPLES],
        dataset_id=ds.id,
    )
    return str(ds.id)


def _target(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the compiled LangGraph workflow and extract structured telemetry."""
    result = app.invoke({"messages": [HumanMessage(content=inputs["question"])]})
    plan = result.get("plan") or {}
    actual_agents = [s.get("agent") for s in plan.get("steps", [])]
    return {
        "answer": result["messages"][-1].content,
        "actual_agents": actual_agents,
        "query_ids": result.get("query_ids", []),
        "sql_metrics": result.get("sql_metrics", []),
        "execution_errors": result.get("execution_errors", []),
    }


def eval_route_match(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]) -> Dict[str, Any]:
    """Verify the supervisor planned the exact expected specialist agent pipeline."""
    expected = reference_outputs.get("expected_agents", [])
    actual = outputs.get("actual_agents", [])
    return {
        "key": "route_match",
        "score": 1.0 if actual == expected else 0.0,
        "comment": f"expected={expected}, actual={actual}",
    }


def eval_sql_executed(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]) -> Dict[str, Any]:
    """Verify at least one live Snowflake SQL query executed and produced rows."""
    metrics: List[Dict[str, Any]] = outputs.get("sql_metrics", [])
    total_rows = sum(m.get("rows", 0) for m in metrics)
    return {
        "key": "sql_executed_with_rows",
        "score": 1.0 if (len(metrics) > 0 and total_rows > 0) else 0.0,
        "comment": f"queries={len(metrics)}, total_rows={total_rows}",
    }


def eval_zero_errors(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]) -> Dict[str, Any]:
    """Verify zero runtime errors were collected by the supervisor."""
    errors = outputs.get("execution_errors", [])
    return {
        "key": "eval_zero_errors",  # the live app already writes its own "zero_errors" feedback
        "score": 1.0 if len(errors) == 0 else 0.0,
        "comment": "ok" if not errors else "; ".join(errors[:3]),
    }


def run_langsmith_eval() -> None:
    """Run LangSmith experiment evaluation and print summary."""
    if not os.getenv("LANGSMITH_API_KEY"):
        print("Skipping LangSmith eval: LANGSMITH_API_KEY is not set.")
        return

    client = Client()
    _ensure_dataset(client)
    print(f"\n=== 1. Running LangSmith Evaluation on '{DATASET_NAME}' ===")
    results = evaluate(
        _target,
        data=DATASET_NAME,
        evaluators=[eval_route_match, eval_sql_executed, eval_zero_errors],
        experiment_prefix="snowlanggraph-trial",
        max_concurrency=1,
        metadata={"mode": "trial_cortex_analyst", "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE")},
    )
    print("LangSmith experiment complete:", getattr(results, "experiment_name", results))


def run_trulens_recording() -> None:
    """Wrap the LangGraph app with TruGraph and record local OTEL trace telemetry."""
    print("\n=== 2. Running TruLens TruGraph Recording ===")
    tru_session = TruSession()
    app_version = f"trial-{uuid.uuid4().hex[:6]}"
    tru_app = TruGraph(
        app,
        app_name="Customer Intelligence Multi-Agent",
        app_version=app_version,
        main_method=app.invoke,
    )
    sample_q = "What is the churn rate and monthly revenue for enterprise customers?"
    with tru_app as recording:
        out = app.invoke({"messages": [HumanMessage(content=sample_q)]})
    record = recording.get()
    print(
        f"TruLens recorded app_version={app_version}, record_id={getattr(record, 'record_id', 'ok')}, "
        f"query_ids={out.get('query_ids', [])}"
    )
    df, _ = tru_session.get_records_and_feedback(app_ids=[tru_app.app_id])
    print(f"TruLens local store records for this run: {len(df)}")


def verify_snowflake_audit_view() -> int:
    """Query AGENT_OBSERVABILITY_LOGS in Snowflake to show end-to-end correlation. Returns the row count."""
    print("\n=== 3. Verifying Snowflake AGENT_OBSERVABILITY_LOGS View ===")
    rows = get_session().sql("""
        SELECT
            QUERY_ID,
            LANGSMITH_TRACE_ID,
            AGENT_NAME,
            STEP_NUMBER,
            TOOL_NAME,
            EXECUTION_STATUS,
            ELAPSED_MS,
            ROWS_PRODUCED
        FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
        ORDER BY START_TIME DESC
        LIMIT 10
    """).collect()
    print(f"Found {len(rows)} correlated query log rows in Snowflake:")
    for r in rows[:6]:
        print(" ", r.as_dict())
    return len(rows)


if __name__ == "__main__":
    run_langsmith_eval()
    run_trulens_recording()
    if verify_snowflake_audit_view() == 0:
        print("FAIL: AGENT_OBSERVABILITY_LOGS returned no correlated rows.")
        sys.exit(1)
