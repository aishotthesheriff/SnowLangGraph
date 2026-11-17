"""
Supervisor graph: state, deterministic planner, agent nodes, synthesis, compiled `app`.

Hub-and-spoke: START -> supervisor (plan) -> agent(s) -> supervisor (route) -> ... -> supervisor (synthesis) -> END
"""

import re
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command

from observability import annotate_run_tree, emit_langsmith_feedback, get_trace_ids, logger
from snowflake_client import (
    AGENT_NAMES,
    DATA_DATABASE,
    DATA_SCHEMA,
    SEMANTIC_VIEWS,
    USE_CORTEX_AGENTS,
    get_cortex_agent,
    run_cortex_analyst,
    run_content_sql_search,
)


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


def is_new_turn(messages: List[BaseMessage]) -> bool:
    """True when the newest message is from the user (a fresh question, not a mid-run step)."""
    if not messages:
        return False
    last = messages[-1]
    if isinstance(last, HumanMessage):
        return True
    return isinstance(last, dict) and last.get("type", last.get("role")) in ("human", "user")


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
# PLANNER & SYNTHESIZER (TRIAL-COMPATIBLE MODE)
# =============================================================================


_CONTENT_TERMS = ("ticket", "feedback", "complain", "sentiment", "support", "issue", "bug", "api")
_ANALYST_TERMS = ("churn", "usage", "session", "engagement", "behavior", "plan", "active", "cust_")
_RESEARCH_TERMS = ("industr", "market", "lifetime", "clv", "ltv", "mrr", "revenue", "segment", "strategic")


def _matched_terms(text: str, terms) -> List[str]:
    """Return the terms that start a word in text ('api' matches 'APIs', not 'rapid')."""
    return [t for t in terms if re.search(rf"\b{re.escape(t)}", text)]


def _has_term(text: str, terms) -> bool:
    """True if any term starts a word in text."""
    return bool(_matched_terms(text, terms))


def _build_trial_plan(query: str) -> Dict:
    """Create a deterministic 1- to 3-step execution plan based on query intent."""
    q = query.lower()
    steps: List[Dict] = []

    matched = {
        "content": _matched_terms(q, _CONTENT_TERMS),
        "analyst": _matched_terms(q, _ANALYST_TERMS),
        "research": _matched_terms(q, _RESEARCH_TERMS),
    }
    needs_content = bool(matched["content"])
    needs_analyst = bool(matched["analyst"])
    needs_research = bool(matched["research"])

    # Fallback: no keyword matched, so the planner sends the question to the data analyst.
    fallback = not (needs_content or needs_analyst or needs_research)
    if fallback:
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
        # Product-level observability: what the user asked about, and whether the planner had to guess.
        "intents": [name for name, terms in matched.items() if terms] or ["analyst"],
        "matched_terms": {name: terms for name, terms in matched.items() if terms},
        "fallback": fallback,
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

    # MODE 1: PLANNING (also runs again when the user sends a new question in the same thread)
    if not has_plan(state) or is_new_turn(messages):
        latest_message = get_latest_human_message(messages)
        if not latest_message:
            return Command(
                update={"plan": {"steps": [], "plan_summary": "No query"}, "planning_complete": True},
                goto="__end__",
            )

        plan = _build_trial_plan(latest_message)
        route_str = "->".join(s["agent"] for s in plan["steps"])
        annotate_run_tree(
            metadata={
                "plan_summary": plan["plan_summary"],
                "total_steps": plan["total_steps"],
                "route": route_str,
                "intents": plan["intents"],
                "matched_terms": plan["matched_terms"],
                "planner_fallback": plan["fallback"],
            },
            tags=[
                "trial_mode" if not USE_CORTEX_AGENTS else "cortex_agents_mode",
                f"route:{route_str}",
                *[f"intent:{i}" for i in plan["intents"]],
                *(["planner_fallback"] if plan["fallback"] else []),
            ],
        )

        logger.info(f"\n{'━'*60}")
        logger.info("📋 EXECUTION PLAN")
        logger.info(f"{'━'*60}")
        logger.info(f"{plan['plan_summary']}")
        for step in plan["steps"]:
            logger.info(f"   {step['step_number']}. {step['agent']} ({step['tool']})")
        logger.info(f"{'━'*60}\n")

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
            logger.info(f"   → Routing to {next_agent}")
            return Command(goto=next_agent)

    # MODE 3: SYNTHESIS
    original_question = get_latest_human_message(messages)
    agent_outputs = state.get("agent_outputs", {})
    execution_errors = state.get("execution_errors", [])
    sql_metrics = state.get("sql_metrics", [])
    trace_id, run_id = get_trace_ids()

    logger.info(f"📊 Synthesizing results from {len(agent_outputs)} agent(s)...")

    content = _synthesize_trial_response(
        original_question,
        plan or {},
        agent_outputs,
        execution_errors,
        sql_metrics,
        trace_id,
    )

    total_rows = sum(m.get("rows", 0) for m in sql_metrics)
    annotate_run_tree(
        metadata={
            "snowflake_query_ids": [m["sfqid"] for m in sql_metrics if m.get("sfqid")],
            "total_sql_rows": total_rows,
            "error_count": len(execution_errors),
        }
    )
    emit_langsmith_feedback(
        run_id=trace_id or run_id,
        scores={
            "execution_success": 1.0 if not execution_errors else 0.0,
            "non_empty_results": 1.0 if total_rows > 0 else 0.0,
            "zero_errors": 1.0 if len(execution_errors) == 0 else 0.0,
            "step_count": float(len((plan or {}).get("steps", []))),
        },
        comment=f"Automated telemetry: {len(sql_metrics)} Snowflake queries, {total_rows} total rows",
    )

    logger.info("✅ Analysis complete\n")
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

    logger.info(f"🤖 {agent_name} analyzing...")
    try:
        if USE_CORTEX_AGENTS:
            res = get_cortex_agent(agent_name).invoke(query)
            response_content = res.get("output", "")
            step_metrics: List[Dict[str, Any]] = []
        else:
            response_content, step_metrics = runner(query, step_num)

        for m in step_metrics:
            if m.get("sfqid"):
                query_ids.append(m["sfqid"])
        sql_metrics.extend(step_metrics)

        logger.info(f"   ✓ Complete ({len(response_content)} chars)")
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
        logger.info(f"   ✗ {error_msg}")
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
        lambda q, step_num: run_content_sql_search(q, agent_name="CONTENT_AGENT", step_number=step_num),
    )


def data_analyst_agent_node(state: State) -> Command[Literal["supervisor"]]:
    """Data Analyst Agent - metrics and behavior analytics via Cortex Analyst."""
    return _execute_agent_step(
        state,
        "DATA_ANALYST_AGENT",
        lambda q, step_num: run_cortex_analyst(
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
        lambda q, step_num: run_cortex_analyst(
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
