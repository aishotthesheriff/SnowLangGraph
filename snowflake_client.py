"""
Snowflake access layer: lazy shared session, Cortex Analyst, support-ticket search, SQL execution.

Every SQL statement carries its own QUERY_TAG (app, LangSmith trace id, agent, step, tool),
which AGENT_OBSERVABILITY_LOGS reads.
"""

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from langsmith import traceable
from snowflake.snowpark import Session

# Fix for langchain_snowflake import compatibility
import langchain.tools
from langchain_core.tools import Tool

langchain.tools.Tool = Tool

from langchain_snowflake import SnowflakeCortexAgent, create_session_from_env

from observability import annotate_run_tree, get_trace_ids

load_dotenv()


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
        secret = os.getenv("SNOWFLAKE_TOKEN") or os.getenv("SNOWFLAKE_PASSWORD")
        if authenticator.upper() == "PROGRAMMATIC_ACCESS_TOKEN":
            if secret:
                cfg["token"] = secret
        elif authenticator.lower() != "externalbrowser" and os.getenv("SNOWFLAKE_PASSWORD"):
            cfg["password"] = os.environ["SNOWFLAKE_PASSWORD"]
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


warehouse = os.getenv("SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_LEARNING_WH")

_session: Optional[Session] = None
_session_lock = threading.RLock()  # re-entrant: get_cortex_agent() holds it while get_session() takes it


def get_session() -> Session:
    """Return the shared Snowpark session. It is created on first use, not at import."""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                new_session = _create_snowflake_session()
                if os.getenv("SNOWFLAKE_ROLE"):
                    new_session.sql(f"USE ROLE {os.environ['SNOWFLAKE_ROLE']}").collect()
                new_session.sql(f"USE WAREHOUSE {warehouse}").collect()
                _session = new_session
    return _session


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


_cortex_agents: Dict[str, SnowflakeCortexAgent] = {}


def get_cortex_agent(name: str) -> SnowflakeCortexAgent:
    """Return the managed Cortex Agent client for `name` (managed mode only). Created on first use."""
    with _session_lock:
        if name not in _cortex_agents:
            _cortex_agents[name] = SnowflakeCortexAgent(
                session=get_session(),
                name=name,
                database=AGENT_DATABASE,
                schema=AGENT_SCHEMA,
                warehouse=warehouse,
            )
        return _cortex_agents[name]


# =============================================================================
# SQL EXECUTION
# =============================================================================


@traceable(run_type="tool", name="snowflake_sql_execute")
def execute_snowflake_sql(
    sql: str,
    params: Optional[List[Any]] = None,
    agent_name: str = "system",
    step_number: int = 0,
    tool_name: str = "sql",
) -> tuple[List[Any], str, float]:
    """Execute SQL on Snowflake with correlated QUERY_TAG and return (rows, sfqid, elapsed_ms)."""
    trace_id, _ = get_trace_ids()
    query_tag = json.dumps({
        "app": "snowlanggraph",
        "trace_id": trace_id or "local",
        "agent": agent_name,
        "step": step_number,
        "tool": tool_name,
    })
    # The tag travels with this one statement (statement_params). The shared session is never changed,
    # so two runs at the same time cannot mix their tags.
    sess = get_session()
    t0 = time.perf_counter()
    df = sess.sql(sql, params=params) if params is not None else sess.sql(sql)
    job = df.collect(block=False, statement_params={"QUERY_TAG": query_tag})
    rows = job.result()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    sfqid = str(getattr(job, "query_id", "") or "")

    annotate_run_tree(
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


_READ_ONLY_SQL = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(?:SELECT|WITH)\b", re.IGNORECASE)


def _extract_customer_ids(text: str) -> List[str]:
    """Extract unique CUST_XXXXXX identifiers preserving order."""
    return list(dict.fromkeys(re.findall(r"CUST_\d+", text.upper())))


def _extract_keywords(text: str) -> List[str]:
    """Extract meaningful search keywords from natural-language text."""
    cleaned = re.sub(r"CUST_\d+", " ", text, flags=re.IGNORECASE)
    tokens = re.findall(r"[a-zA-Z0-9_]{3,}", cleaned.lower())
    return list(dict.fromkeys(t for t in tokens if t not in _STOPWORDS))[:5]


def format_rows_markdown(rows: List, max_rows: int = 12) -> str:
    """Format Snowpark Row objects into a concise markdown table."""
    if not rows:
        return "_No matching rows returned._"
    cols = list(rows[0].as_dict().keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    body = []
    for row in rows[:max_rows]:
        d = row.as_dict()
        vals = [str(d[c]).replace("\n", " ")[:120].replace("|", "\\|") for c in cols]
        body.append("| " + " | ".join(vals) + " |")
    extra = f"\n_Showing {min(len(rows), max_rows)} of {len(rows)} rows._" if len(rows) > max_rows else ""
    return "\n".join([header, sep, *body]) + extra


@traceable(run_type="llm", name="cortex_analyst_text_to_sql")
def run_cortex_analyst(
    semantic_view: str,
    question: str,
    agent_name: str = "ANALYST",
    step_number: int = 0,
) -> tuple[str, List[Dict[str, Any]]]:
    """Query Snowflake Cortex Analyst REST API and execute the generated SQL."""
    conn = get_session().connection
    token = conn.rest.token
    host = conn.host
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
    if not resp.ok:
        raise RuntimeError(f"Cortex Analyst HTTP {resp.status_code}: {resp.text[:500]}")
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

    if generated_sql and not _READ_ONLY_SQL.match(generated_sql):
        sections.append("_Skipped generated SQL because it is not a read-only SELECT/WITH statement._")
        generated_sql = None
    elif generated_sql:
        sections.append(f"**Generated SQL (`{semantic_view}`):**\n```sql\n{generated_sql.strip()}\n```")
        rows, sfqid, sql_ms = execute_snowflake_sql(
            generated_sql,
            agent_name=agent_name,
            step_number=step_number,
            tool_name="CORTEX_ANALYST_SQL",
        )
        sections.append("**Query Results:**\n" + format_rows_markdown(rows))
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

    annotate_run_tree(
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
def run_content_sql_search(
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
            COUNT(CASE WHEN priority IN ('high', 'urgent') THEN 1 END) AS urgent_tickets,
            ROUND(AVG(resolution_time_hours), 1) AS avg_resolution_hours
        FROM {table}
        {where_sql}
    """
    summary_rows, qid_summary, ms_summary = execute_snowflake_sql(
        summary_sql,
        params=params,
        agent_name=agent_name,
        step_number=step_number,
        tool_name="SUPPORT_TICKETS_SUMMARY",
    )

    if not customer_ids and where_clauses and summary_rows[0]["TOTAL_MATCHING_TICKETS"] == 0:
        where_sql = ""
        params = []
        summary_rows, qid_summary, ms_summary = execute_snowflake_sql(
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
            COUNT(CASE WHEN priority IN ('high', 'urgent') THEN 1 END) AS urgent_count
        FROM {table}
        {where_sql}
        GROUP BY category
        ORDER BY ticket_count DESC
        LIMIT 8
    """
    breakdown_rows, qid_breakdown, ms_breakdown = execute_snowflake_sql(
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
    sample_rows, qid_samples, ms_samples = execute_snowflake_sql(
        sample_sql,
        params=params,
        agent_name=agent_name,
        step_number=step_number,
        tool_name="SUPPORT_TICKETS_SAMPLES",
    )

    matched_cust_ids = list(dict.fromkeys(r["CUSTOMER_ID"] for r in sample_rows))
    sections = [
        note + "**Support Ticket Summary:**\n" + format_rows_markdown(summary_rows),
        "**Category Breakdown:**\n" + format_rows_markdown(breakdown_rows),
        "**Representative Tickets:**\n" + format_rows_markdown(sample_rows),
    ]
    if matched_cust_ids:
        sections.append("**Sample Affected Customer IDs:** " + ", ".join(matched_cust_ids[:8]))

    metrics = [
        {"agent": agent_name, "tool": "SUPPORT_TICKETS_SUMMARY", "sfqid": qid_summary, "sql_ms": ms_summary, "rows": len(summary_rows)},
        {"agent": agent_name, "tool": "SUPPORT_TICKETS_BREAKDOWN", "sfqid": qid_breakdown, "sql_ms": ms_breakdown, "rows": len(breakdown_rows)},
        {"agent": agent_name, "tool": "SUPPORT_TICKETS_SAMPLES", "sfqid": qid_samples, "sql_ms": ms_samples, "rows": len(sample_rows)},
    ]
    annotate_run_tree(
        metadata={
            "extracted_customer_ids": customer_ids,
            "extracted_keywords": keywords,
            "keyword_fallback": bool(note),
            "matching_tickets": int(summary_rows[0]["TOTAL_MATCHING_TICKETS"]),
            "customers_affected": int(summary_rows[0]["CUSTOMERS_AFFECTED"]),
        },
    )
    return "\n\n".join(sections), metrics
