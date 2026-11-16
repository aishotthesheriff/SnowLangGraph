"""
Multi-Agent Supervisor Workflow for LangGraph Studio (Observability-Instrumented Trial Mode)

A hub-and-spoke architecture where a supervisor coordinates specialized agents
backed by live Snowflake data and Cortex Analyst Semantic Views:
    START → supervisor (planning) → Agent(s) → supervisor (routing) → ... → supervisor (synthesis) → END

Observability & Trial-Org Compatibility:
- Nested LangSmith @traceable spans for Cortex Analyst (llm), Support Ticket Search (retriever),
  and Snowflake SQL execution (tool) with sfqid + request_id metadata.
- Correlated Snowflake QUERY_TAGs (app, langsmith trace_id, agent, step, tool) queried via
  CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS.
- Programmatic LangSmith feedback scores emitted on completion (execution_success, non_empty_results,
  zero_errors, step_count).
- Set SNOWFLAKE_USE_CORTEX_AGENTS=true in .env when running on an account with full Cortex AI enabled.
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Literal, Optional

import requests
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command
from langsmith import Client as LangSmithClient
from langsmith import get_current_run_tree, traceable
from snowflake.snowpark import Session

# Fix for langchain_snowflake import compatibility
import langchain.tools
from langchain_core.tools import Tool

langchain.tools.Tool = Tool

from langchain_snowflake import ChatSnowflake, SnowflakeCortexAgent, create_session_from_env

load_dotenv()


# =============================================================================
# STATE DEFINITION
# =============================================================================


class State(MessagesState):
    """Extended state for multi-step supervisor execution & observability tracking."""

    plan: Optional[Dict] = None
    current_step: int = 0
    agent_outputs: Dict = {}
    execution_errors: List = []
    planning_complete: bool = False
    query_ids: List[str] = []
    sql_metrics: List[Dict[str, Any]] = []


# =============================================================================
# SNOWFLAKE SESSION & CONFIGURATION
# =============================================================================


def _create_snowflake_session() -> Session:
    """Create a Snowpark session supporting password/PAT or externalbrowser SSO."""
    authenticator = os.getenv("SNOWFLAKE_AUTHENTICATOR")
    if authenticator:
        cfg = {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_USER"],
            "authenticator": authenticator,
        }
        for key, env_var in (
            ("role", "SNOWFLAKE_ROLE"),
            ("warehouse", "SNOWFLAKE_WAREHOUSE"),
            ("database", "SNOWFLAKE_DATABASE"),
            ("schema", "SNOWFLAKE_SCHEMA"),
        ):
            if os.getenv(env_var):
                cfg[key] = os.environ[env_var]
        return Session.builder.configs(cfg).create()
    return create_session_from_env()


session = _create_snowflake_session()

warehouse = os.getenv("SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_LEARNING_WH")
if os.getenv("SNOWFLAKE_ROLE"):
    session.sql(f"USE ROLE {os.environ['SNOWFLAKE_ROLE']}").collect()
session.sql(f"USE WAREHOUSE {warehouse}").collect()

DATA_DATABASE = os.getenv("SNOWFLAKE_DATABASE", "CUSTOMER_INTELLIGENCE_DB")
DATA_SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")
AGENT_DATABASE = os.getenv("SNOWFLAKE_AGENT_DATABASE", "SNOWFLAKE_INTELLIGENCE")
AGENT_SCHEMA = os.getenv("SNOWFLAKE_AGENT_SCHEMA", "AGENTS")
AGENT_NAMES = ["CONTENT_AGENT", "DATA_ANALYST_AGENT", "RESEARCH_AGENT"]

USE_CORTEX_AGENTS = os.getenv("SNOWFLAKE_USE_CORTEX_AGENTS", "false").lower() == "true"

SEMANTIC_VIEWS = {
    "DATA_ANALYST_AGENT": f"{DATA_DATABASE}.{DATA_SCHEMA}.CUSTOMER_BEHAVIOR_ANALYST",
    "RESEARCH_AGENT": f"{DATA_DATABASE}.{DATA_SCHEMA}.STRATEGIC_RESEARCH_ANALYST",
}

_langsmith_client: Optional[LangSmithClient] = None
if os.getenv("LANGSMITH_API_KEY"):
    try:
        _langsmith_client = LangSmithClient()
    except Exception:
        _langsmith_client = None

if USE_CORTEX_AGENTS:
    supervisor_model = ChatSnowflake(
        session=session,
        model="claude-4-sonnet",
        temperature=0.1,
        max_tokens=2000,
    )
    cortex_agents = {
        name: SnowflakeCortexAgent(
            session=session,
            name=name,
            database=AGENT_DATABASE,
            schema=AGENT_SCHEMA,
            warehouse=warehouse,
        )
        for name in AGENT_NAMES
    }
else:
    supervisor_model = None
    cortex_agents = {}


# =============================================================================
# OBSERVABILITY HELPERS (LANGSMITH + SNOWFLAKE QUERY_TAG)
# =============================================================================


def _get_trace_ids() -> tuple[Optional[str], Optional[str]]:
    """Return (trace_id, run_id) from the active LangSmith run tree if tracing."""
    try:
        rt = get_current_run_tree()
        if rt:
            trace_id = str(getattr(rt, "trace_id", None) or rt.id)
            run_id = str(rt.id)
            return trace_id, run_id
    except Exception:
        pass
    return None, None


def _annotate_run_tree(metadata: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None) -> None:
    """Attach structured metadata/tags to the active LangSmith span safely."""
    try:
        rt = get_current_run_tree()
        if not rt:
            return
        if metadata:
            rt.metadata.update(metadata)
        if tags:
            existing = list(getattr(rt, "tags", None) or [])
            for tag in tags:
                if tag not in existing:
                    existing.append(tag)
            rt.tags = existing
    except Exception:
        pass


def _emit_langsmith_feedback(run_id: Optional[str], scores: Dict[str, float], comment: str) -> None:
    """Send deterministic run-level feedback scores to LangSmith non-destructively."""
    if not (_langsmith_client and run_id):
        return
    for key, value in scores.items():
        try:
            _langsmith_client.create_feedback(
                run_id=run_id,
                key=key,
                score=float(value),
                comment=comment,
            )
        except Exception:
            pass


@traceable(run_type="tool", name="snowflake_sql_execute")
def _execute_snowflake_sql(
    sql: str,
    params: Optional[List[Any]] = None,
    agent_name: str = "system",
    step_number: int = 0,
    tool_name: str = "sql",
) -> tuple[List[Any], str, float]:
    """Execute SQL on Snowflake with correlated QUERY_TAG and return (rows, sfqid, elapsed_ms)."""
    trace_id, _ = _get_trace_ids()
    query_tag = json.dumps({
        "app": "snowlanggraph",
        "trace_id": trace_id or "local",
        "agent": agent_name,
        "step": step_number,
        "tool": tool_name,
    })
    session.query_tag = query_tag

    t0 = time.perf_counter()
    try:
        df = session.sql(sql, params=params) if params is not None else session.sql(sql)
        job = df.collect(block=False)
        rows = job.result()
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        sfqid = str(getattr(job, "query_id", "") or "")
    finally:
        session.query_tag = ""

    _annotate_run_tree(
        metadata={
            "snowflake_query_id": sfqid,
            "snowflake_query_tag": query_tag,
            "elapsed_ms": elapsed_ms,
            "row_count": len(rows),
            "warehouse": warehouse,
        },
        tags=[f"agent:{agent_name}", f"tool:{tool_name}"],
    )
    return rows, sfqid, elapsed_ms


# =============================================================================
# MESSAGE & STATE HELPERS
# =============================================================================


def get_latest_human_message(messages: List[BaseMessage]) -> str:
    """Extract the latest human message text from LangGraph messages."""
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage):
            content = msg.content
        elif isinstance(msg, dict) and msg.get("type", msg.get("role")) in ("human", "user"):
            content = msg.get("content", "")
        else:
            continue

        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return item.get("text", "")
                if isinstance(item, str):
                    return item
        return str(content)
    return ""


def has_plan(state: State) -> bool:
    return state.get("plan") is not None and state.get("planning_complete", False)


def get_current_step(state: State) -> Optional[Dict]:
    plan = state.get("plan") or {}
    steps = plan.get("steps", [])
    idx = state.get("current_step", 0)
    return steps[idx] if idx < len(steps) else None


def is_plan_complete(state: State) -> bool:
    plan = state.get("plan") or {}
    steps = plan.get("steps", [])
    return state.get("current_step", 0) >= len(steps)


def build_query_with_context(original_query: str, state: State) -> str:
    """Build step query enriched with outputs from earlier steps if referenced."""
    step = get_current_step(state)
    if not step:
        return original_query

    uses_data_from = step.get("uses_data_from", [])
    step_query = step.get("consolidated_query") or original_query
    if not uses_data_from:
        return step_query

    agent_outputs = state.get("agent_outputs", {})
    steps = (state.get("plan") or {}).get("steps", [])
    context_parts = []
    for step_num in uses_data_from:
        for s in steps:
            if s.get("step_number") == step_num and s.get("agent") in agent_outputs:
                text = agent_outputs[s["agent"]]
                context_parts.append(f"From {s['agent']}: {text[:1500]}")
                break

    if context_parts:
        return f"{step_query}\n\nContext from previous analysis:\n" + "\n".join(context_parts)
    return step_query


# =============================================================================
# LIVE SNOWFLAKE TRIAL-COMPATIBLE AGENT IMPLEMENTATIONS
# =============================================================================

_STOPWORDS = {
    "a", "about", "all", "an", "and", "analyze", "are", "as", "at", "be",
    "best", "by", "can", "check", "compare", "complaining", "complaints",
    "customer", "customers", "do", "find", "for", "from", "get", "have",
    "highest", "how", "in", "is", "issue", "issues", "it", "list", "look",
    "many", "monthly", "most", "of", "on", "or", "our", "problem", "problems",
    "rate", "revenue", "churn", "show", "tell", "that", "the", "their", "them",
    "they", "this", "to", "us", "what", "which", "who", "with", "would",
}


def _extract_customer_ids(text: str) -> List[str]:
    """Extract unique CUST_XXXXXX identifiers preserving order."""
    return list(dict.fromkeys(re.findall(r"CUST_\d+", text.upper())))


def _extract_keywords(text: str) -> List[str]:
    """Extract meaningful search keywords from natural-language text."""
    cleaned = re.sub(r"CUST_\d+", " ", text, flags=re.IGNORECASE)
    tokens = re.findall(r"[a-zA-Z0-9_]{3,}", cleaned.lower())
    return list(dict.fromkeys(t for t in tokens if t not in _STOPWORDS))[:5]


def _format_rows_markdown(rows: List, max_rows: int = 12) -> str:
    """Format Snowpark Row objects into a concise markdown table."""
    if not rows:
        return "_No matching rows returned._"
    cols = list(rows[0].as_dict().keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    body = []
    for row in rows[:max_rows]:
        d = row.as_dict()
        vals = [str(d[c]).replace("\n", " ")[:120] for c in cols]
        body.append("| " + " | ".join(vals) + " |")
    extra = f"\n_Showing {min(len(rows), max_rows)} of {len(rows)} rows._" if len(rows) > max_rows else ""
    return "\n".join([header, sep, *body]) + extra


@traceable(run_type="llm", name="cortex_analyst_text_to_sql")
def _run_cortex_analyst(
    semantic_view: str,
    question: str,
    agent_name: str = "ANALYST",
    step_number: int = 0,
) -> tuple[str, List[Dict[str, Any]]]:
    """Query Snowflake Cortex Analyst REST API and execute the generated SQL."""
    token = session.connection.rest.token
    host = session.connection.host
    headers = {
        "Authorization": f'Snowflake Token="{token}"',
        "Content-Type": "application/json",
    }
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
        "semantic_view": semantic_view,
    }
    t0 = time.perf_counter()
    resp = requests.post(
        f"https://{host}/api/v2/cortex/analyst/message",
        headers=headers,
        json=payload,
        timeout=45,
    )
    analyst_ms = round((time.perf_counter() - t0) * 1000, 1)
    resp.raise_for_status()
    data = resp.json()
    cortex_request_id = data.get("request_id") or resp.headers.get("x-snowflake-request-id", "")

    interpretations: List[str] = []
    generated_sql: Optional[str] = None
    for block in data.get("message", {}).get("content", []):
        if block.get("type") == "text":
            interpretations.append(block.get("text", ""))
        elif block.get("type") == "sql":
            generated_sql = block.get("statement")

    sections: List[str] = []
    metrics: List[Dict[str, Any]] = []
    if interpretations:
        sections.append("**Cortex Analyst Interpretation:**\n" + "\n".join(interpretations))

    if generated_sql:
        sections.append(f"**Generated SQL (`{semantic_view}`):**\n```sql\n{generated_sql.strip()}\n```")
        rows, sfqid, sql_ms = _execute_snowflake_sql(
            generated_sql,
            agent_name=agent_name,
            step_number=step_number,
            tool_name="CORTEX_ANALYST_SQL",
        )
        sections.append("**Query Results:**\n" + _format_rows_markdown(rows))
        metrics.append({
            "agent": agent_name,
            "tool": "CORTEX_ANALYST",
            "sfqid": sfqid,
            "cortex_request_id": cortex_request_id,
            "analyst_ms": analyst_ms,
            "sql_ms": sql_ms,
            "rows": len(rows),
        })
    else:
        sections.append("_Cortex Analyst did not emit SQL for this question._")

    _annotate_run_tree(
        metadata={
            "semantic_view": semantic_view,
            "cortex_request_id": cortex_request_id,
            "analyst_latency_ms": analyst_ms,
            "generated_sql": generated_sql,
        },
        tags=[f"semantic_view:{semantic_view.split('.')[-1]}"],
    )
    return "\n\n".join(sections), metrics


@traceable(run_type="retriever", name="snowflake_support_tickets_search")
def _run_content_sql_search(
    question: str,
    agent_name: str = "CONTENT_AGENT",
    step_number: int = 1,
) -> tuple[str, List[Dict[str, Any]]]:
    """Query SUPPORT_TICKETS via parameterized SQL for feedback, sentiment, and issues."""
    table = f"{DATA_DATABASE}.{DATA_SCHEMA}.SUPPORT_TICKETS"
    customer_ids = _extract_customer_ids(question)
    keywords = _extract_keywords(question)

    where_clauses: List[str] = []
    params: List[str] = []

    if customer_ids:
        placeholders = ", ".join(["?"] * len(customer_ids))
        where_clauses.append(f"customer_id IN ({placeholders})")
        params.extend(customer_ids)
    elif keywords:
        kw_clauses = []
        for kw in keywords:
            like_val = f"%{kw}%"
            kw_clauses.append("(LOWER(ticket_text) LIKE ? OR LOWER(category) LIKE ?)")
            params.extend([like_val, like_val])
        where_clauses.append("(" + " OR ".join(kw_clauses) + ")")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    summary_sql = f"""
        SELECT
            COUNT(*) AS total_matching_tickets,
            COUNT(DISTINCT customer_id) AS customers_affected,
            ROUND(AVG(satisfaction_score), 2) AS avg_satisfaction,
            COUNT(CASE WHEN priority IN ('high', 'critical') THEN 1 END) AS urgent_tickets,
            ROUND(AVG(resolution_time_hours), 1) AS avg_resolution_hours
        FROM {table}
        {where_sql}
    """
    summary_rows, qid_summary, ms_summary = _execute_snowflake_sql(
        summary_sql,
        params=params,
        agent_name=agent_name,
        step_number=step_number,
        tool_name="SUPPORT_TICKETS_SUMMARY",
    )

    if not customer_ids and where_clauses and summary_rows[0]["TOTAL_MATCHING_TICKETS"] == 0:
        where_sql = ""
        params = []
        summary_rows, qid_summary, ms_summary = _execute_snowflake_sql(
            summary_sql.replace(where_clauses[0], "1=1"),
            agent_name=agent_name,
            step_number=step_number,
            tool_name="SUPPORT_TICKETS_SUMMARY_FALLBACK",
        )
        note = f"_No exact keyword match for {keywords}; showing overall support ticket intelligence._\n\n"
    else:
        note = ""

    breakdown_sql = f"""
        SELECT
            category,
            COUNT(*) AS ticket_count,
            ROUND(AVG(satisfaction_score), 2) AS avg_csat,
            COUNT(CASE WHEN priority IN ('high', 'critical') THEN 1 END) AS urgent_count
        FROM {table}
        {where_sql}
        GROUP BY category
        ORDER BY ticket_count DESC
        LIMIT 8
    """
    breakdown_rows, qid_breakdown, ms_breakdown = _execute_snowflake_sql(
        breakdown_sql,
        params=params,
        agent_name=agent_name,
        step_number=step_number,
        tool_name="SUPPORT_TICKETS_BREAKDOWN",
    )

    sample_sql = f"""
        SELECT
            ticket_id,
            customer_id,
            category,
            priority,
            satisfaction_score,
            LEFT(ticket_text, 140) AS ticket_excerpt
        FROM {table}
        {where_sql}
        ORDER BY created_date DESC
        LIMIT 8
    """
    sample_rows, qid_samples, ms_samples = _execute_snowflake_sql(
        sample_sql,
        params=params,
        agent_name=agent_name,
        step_number=step_number,
        tool_name="SUPPORT_TICKETS_SAMPLES",
    )

    matched_cust_ids = list(dict.fromkeys(r["CUSTOMER_ID"] for r in sample_rows))
    sections = [
        note + "**Support Ticket Summary:**\n" + _format_rows_markdown(summary_rows),
        "**Category Breakdown:**\n" + _format_rows_markdown(breakdown_rows),
        "**Representative Tickets:**\n" + _format_rows_markdown(sample_rows),
    ]
    if matched_cust_ids:
        sections.append("**Sample Affected Customer IDs:** " + ", ".join(matched_cust_ids[:8]))

    metrics = [
        {"agent": agent_name, "tool": "SUPPORT_TICKETS_SUMMARY", "sfqid": qid_summary, "sql_ms": ms_summary, "rows": len(summary_rows)},
        {"agent": agent_name, "tool": "SUPPORT_TICKETS_BREAKDOWN", "sfqid": qid_breakdown, "sql_ms": ms_breakdown, "rows": len(breakdown_rows)},
        {"agent": agent_name, "tool": "SUPPORT_TICKETS_SAMPLES", "sfqid": qid_samples, "sql_ms": ms_samples, "rows": len(sample_rows)},
    ]
    _annotate_run_tree(
        metadata={
            "extracted_customer_ids": customer_ids,
            "extracted_keywords": keywords,
            "matching_tickets": int(summary_rows[0]["TOTAL_MATCHING_TICKETS"]),
            "customers_affected": int(summary_rows[0]["CUSTOMERS_AFFECTED"]),
        },
    )
    return "\n\n".join(sections), metrics


# =============================================================================
# PLANNER & SYNTHESIZER (TRIAL-COMPATIBLE MODE)
# =============================================================================


def _build_trial_plan(query: str) -> Dict:
    """Create a deterministic 1- to 3-step execution plan based on query intent."""
    q = query.lower()
    steps: List[Dict] = []

    needs_content = any(
        w in q for w in ("ticket", "feedback", "complaint", "sentiment", "support", "issue", "bug", "api")
    )
    needs_analyst = any(
        w in q for w in ("churn", "usage", "session", "engagement", "behavior", "plan", "active", "cust_")
    )
    needs_research = any(
        w in q for w in ("industry", "industries", "market", "lifetime", "clv", "ltv", "mrr", "revenue", "segment", "strategic")
    )

    if not (needs_content or needs_analyst or needs_research):
        needs_analyst = True

    if needs_content:
        steps.append({
            "step_number": len(steps) + 1,
            "agent": "CONTENT_AGENT",
            "tool": "SUPPORT_TICKETS_SQL_SEARCH",
            "data_source": f"{DATA_DATABASE}.{DATA_SCHEMA}.SUPPORT_TICKETS",
            "purpose": "Search support tickets and customer feedback",
            "consolidated_query": query,
            "uses_data_from": [],
        })
    if needs_analyst:
        steps.append({
            "step_number": len(steps) + 1,
            "agent": "DATA_ANALYST_AGENT",
            "tool": "CORTEX_ANALYST",
            "data_source": SEMANTIC_VIEWS["DATA_ANALYST_AGENT"],
            "purpose": "Analyze customer behavior, usage, and churn metrics via Semantic View",
            "consolidated_query": query,
            "uses_data_from": [1] if needs_content else [],
        })
    if needs_research:
        steps.append({
            "step_number": len(steps) + 1,
            "agent": "RESEARCH_AGENT",
            "tool": "CORTEX_ANALYST",
            "data_source": SEMANTIC_VIEWS["RESEARCH_AGENT"],
            "purpose": "Analyze strategic revenue, CLV, and industry trends via Semantic View",
            "consolidated_query": query,
            "uses_data_from": [s["step_number"] for s in steps],
        })

    for i, step in enumerate(steps):
        step["next_agent"] = steps[i + 1]["agent"] if i + 1 < len(steps) else None

    agents_used = " -> ".join(s["agent"] for s in steps)
    return {
        "plan_summary": f"Executing {len(steps)} step(s) via {agents_used}",
        "total_steps": len(steps),
        "steps": steps,
    }


def _synthesize_trial_response(
    question: str,
    plan: Dict,
    agent_outputs: Dict[str, str],
    errors: List[str],
    sql_metrics: List[Dict[str, Any]],
    trace_id: Optional[str],
) -> str:
    """Combine live agent outputs and observability telemetry into an executive brief."""
    parts = [
        f"## Summary\nAnswered **{question}** using {plan.get('plan_summary', 'multi-agent workflow')} against live Snowflake data in `{DATA_DATABASE}.{DATA_SCHEMA}`.",
    ]
    for step in plan.get("steps", []):
        agent_name = step["agent"]
        if agent_name in agent_outputs:
            parts.append(f"## {agent_name} ({step.get('purpose', '')})\n{agent_outputs[agent_name]}")
    if errors:
        parts.append("## Execution Notes\n" + "\n".join(f"- {err}" for err in errors))

    if sql_metrics or trace_id:
        total_sql_ms = round(sum(m.get("sql_ms", 0.0) for m in sql_metrics), 1)
        sfqids = [m["sfqid"] for m in sql_metrics if m.get("sfqid")]
        parts.append(
            "## Observability Telemetry\n"
            f"- **LangSmith Trace ID**: `{trace_id or 'local-no-trace'}`\n"
            f"- **Snowflake Queries Executed**: `{len(sfqids)}` (`{total_sql_ms} ms` total SQL time)\n"
            f"- **Snowflake Query IDs (`sfqid`)**: {', '.join(f'`{q}`' for q in sfqids) if sfqids else 'none'}\n"
            f"- **Snowflake Audit View**: `SELECT * FROM {DATA_DATABASE}.{DATA_SCHEMA}.AGENT_OBSERVABILITY_LOGS WHERE LANGSMITH_TRACE_ID = '{trace_id or 'local'}'`"
        )
    return "\n\n".join(parts)


# =============================================================================
# GRAPH NODES
# =============================================================================


def supervisor_node(state: State) -> Command[Literal["CONTENT_AGENT", "DATA_ANALYST_AGENT", "RESEARCH_AGENT", "__end__"]]:
    """Supervisor node: Planning → Routing → Synthesis."""
    messages = state.get("messages", [])
    plan = state.get("plan")

    # MODE 1: PLANNING
    if not has_plan(state):
        latest_message = get_latest_human_message(messages)
        if not latest_message:
            return Command(
                update={"plan": {"steps": [], "plan_summary": "No query"}, "planning_complete": True},
                goto="__end__",
            )

        plan = _build_trial_plan(latest_message)
        route_str = "->".join(s["agent"] for s in plan["steps"])
        _annotate_run_tree(
            metadata={"plan_summary": plan["plan_summary"], "total_steps": plan["total_steps"], "route": route_str},
            tags=["trial_mode" if not USE_CORTEX_AGENTS else "cortex_agents_mode", f"route:{route_str}"],
        )

        print(f"\n{'━'*60}")
        print("📋 EXECUTION PLAN")
        print(f"{'━'*60}")
        print(f"{plan['plan_summary']}")
        for step in plan["steps"]:
            print(f"   {step['step_number']}. {step['agent']} ({step['tool']})")
        print(f"{'━'*60}\n")

        first_agent = plan["steps"][0]["agent"] if plan["steps"] else "__end__"
        return Command(
            update={
                "plan": plan,
                "current_step": 0,
                "planning_complete": True,
                "agent_outputs": {},
                "execution_errors": [],
                "query_ids": [],
                "sql_metrics": [],
            },
            goto=first_agent,
        )

    # MODE 2: ROUTING
    if not is_plan_complete(state):
        current_step = get_current_step(state)
        if current_step and current_step.get("agent") in AGENT_NAMES:
            next_agent = current_step["agent"]
            print(f"   → Routing to {next_agent}")
            return Command(goto=next_agent)

    # MODE 3: SYNTHESIS
    original_question = get_latest_human_message(messages)
    agent_outputs = state.get("agent_outputs", {})
    execution_errors = state.get("execution_errors", [])
    sql_metrics = state.get("sql_metrics", [])
    trace_id, run_id = _get_trace_ids()

    print(f"📊 Synthesizing results from {len(agent_outputs)} agent(s)...")

    content = _synthesize_trial_response(
        original_question,
        plan or {},
        agent_outputs,
        execution_errors,
        sql_metrics,
        trace_id,
    )

    total_rows = sum(m.get("rows", 0) for m in sql_metrics)
    _annotate_run_tree(
        metadata={
            "snowflake_query_ids": [m["sfqid"] for m in sql_metrics if m.get("sfqid")],
            "total_sql_rows": total_rows,
            "error_count": len(execution_errors),
        }
    )
    _emit_langsmith_feedback(
        run_id=run_id,
        scores={
            "execution_success": 1.0 if not execution_errors else 0.0,
            "non_empty_results": 1.0 if total_rows > 0 else 0.0,
            "zero_errors": 1.0 if len(execution_errors) == 0 else 0.0,
            "step_count": float(len((plan or {}).get("steps", []))),
        },
        comment=f"Automated telemetry: {len(sql_metrics)} Snowflake queries, {total_rows} total rows",
    )

    print("✅ Analysis complete\n")
    return Command(
        update={"messages": [AIMessage(content=content, name="supervisor")]},
        goto="__end__",
    )


def _execute_agent_step(state: State, agent_name: str, runner) -> Command[Literal["supervisor"]]:
    """Shared execution wrapper for all specialized agent nodes."""
    query = build_query_with_context(get_latest_human_message(state["messages"]), state)
    current_step_idx = state.get("current_step", 0)
    step_num = current_step_idx + 1
    agent_outputs = state.get("agent_outputs", {}).copy()
    execution_errors = state.get("execution_errors", []).copy()
    query_ids = list(state.get("query_ids", []))
    sql_metrics = list(state.get("sql_metrics", []))

    print(f"🤖 {agent_name} analyzing...")
    try:
        if USE_CORTEX_AGENTS and agent_name in cortex_agents:
            res = cortex_agents[agent_name].invoke(query)
            response_content = res.get("output", "")
            step_metrics: List[Dict[str, Any]] = []
        else:
            response_content, step_metrics = runner(query, step_num)

        for m in step_metrics:
            if m.get("sfqid"):
                query_ids.append(m["sfqid"])
        sql_metrics.extend(step_metrics)

        print(f"   ✓ Complete ({len(response_content)} chars)")
        agent_outputs[agent_name] = response_content
        return Command(
            update={
                "messages": [AIMessage(content=response_content, name=agent_name)],
                "current_step": current_step_idx + 1,
                "agent_outputs": agent_outputs,
                "execution_errors": execution_errors,
                "query_ids": query_ids,
                "sql_metrics": sql_metrics,
            },
            goto="supervisor",
        )
    except Exception as exc:
        error_msg = f"{agent_name} error: {exc}"
        print(f"   ✗ {error_msg}")
        execution_errors.append(error_msg)
        return Command(
            update={
                "messages": [AIMessage(content=f"Error: {exc}", name=agent_name)],
                "current_step": current_step_idx + 1,
                "agent_outputs": agent_outputs,
                "execution_errors": execution_errors,
                "query_ids": query_ids,
                "sql_metrics": sql_metrics,
            },
            goto="supervisor",
        )


def content_agent_node(state: State) -> Command[Literal["supervisor"]]:
    """Content Agent - customer feedback and support ticket analysis."""
    return _execute_agent_step(
        state,
        "CONTENT_AGENT",
        lambda q, step_num: _run_content_sql_search(q, agent_name="CONTENT_AGENT", step_number=step_num),
    )


def data_analyst_agent_node(state: State) -> Command[Literal["supervisor"]]:
    """Data Analyst Agent - metrics and behavior analytics via Cortex Analyst."""
    return _execute_agent_step(
        state,
        "DATA_ANALYST_AGENT",
        lambda q, step_num: _run_cortex_analyst(
            SEMANTIC_VIEWS["DATA_ANALYST_AGENT"],
            q,
            agent_name="DATA_ANALYST_AGENT",
            step_number=step_num,
        ),
    )


def research_agent_node(state: State) -> Command[Literal["supervisor"]]:
    """Research Agent - market and strategic intelligence via Cortex Analyst."""
    return _execute_agent_step(
        state,
        "RESEARCH_AGENT",
        lambda q, step_num: _run_cortex_analyst(
            SEMANTIC_VIEWS["RESEARCH_AGENT"],
            q,
            agent_name="RESEARCH_AGENT",
            step_number=step_num,
        ),
    )


# =============================================================================
# BUILD AND COMPILE GRAPH
# =============================================================================

workflow = StateGraph(State)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("CONTENT_AGENT", content_agent_node)
workflow.add_node("DATA_ANALYST_AGENT", data_analyst_agent_node)
workflow.add_node("RESEARCH_AGENT", research_agent_node)
workflow.add_edge(START, "supervisor")

app = workflow.compile()


if __name__ == "__main__":
    test_queries = [
        "What industries have the highest total MRR and average customer lifetime?",
        "Find customers complaining about API issues and analyze their churn rate and monthly revenue",
    ]
    for test_query in test_queries:
        print(f"\n{'='*60}\nTesting: {test_query}\n{'='*60}\n")
        result = app.invoke({"messages": [HumanMessage(content=test_query)]})
        print(result["messages"][-1].content)
