# Graph Report - SnowLangGraph  (2026-11-16)

## Corpus Check
- Corpus is ~9,024 words - fits in a single context window. You may not need a graph.

## Summary
- 130 nodes · 187 edges · 12 communities (8 shown, 4 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 13 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Synthetic Data Generation
- App Imports & Session Setup
- Snowflake Tables & Semantic Views
- LangGraph Supervisor & Agent Nodes
- Project Docs & Core Frameworks
- Cortex Analyst & SQL Helpers
- Architecture Diagram Components
- LangGraph Studio Config
- LangChain Core Package
- LangChain Snowflake Package
- Python Dotenv Package
- Snowpark Python Package

## God Nodes (most connected - your core abstractions)
1. `State` - 12 edges
2. `main()` - 11 edges
3. `supervisor_node()` - 10 edges
4. `_execute_agent_step()` - 9 edges
5. `_run_cortex_analyst()` - 6 edges
6. `_run_content_sql_search()` - 6 edges
7. `content_agent_node()` - 6 edges
8. `data_analyst_agent_node()` - 6 edges
9. `research_agent_node()` - 6 edges
10. `get_latest_human_message()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `Multi-Agent Customer Intelligence System` --conceptually_related_to--> `SnowLangGraph Multi-Agent AI System`  [INFERRED]
  DEMO_SUMMARY_AND_TALK_TRACK.md → README.md
- `LangSmith Studio` --semantically_similar_to--> `trulens-apps-langgraph`  [INFERRED] [semantically similar]
  DEMO_SUMMARY_AND_TALK_TRACK.md → requirements.txt
- `langgraph` --conceptually_related_to--> `LangGraph`  [INFERRED]
  requirements.txt → DEMO_SUMMARY_AND_TALK_TRACK.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Supervisor Multi-Agent Routing Flow** — demo_summary_and_talk_track_supervisor_node, demo_summary_and_talk_track_content_agent, demo_summary_and_talk_track_data_analyst_agent, demo_summary_and_talk_track_research_agent [EXTRACTED 1.00]
- **CUSTOMER_INTELLIGENCE_DB Core Tables** — demo_summary_and_talk_track_customers_table, demo_summary_and_talk_track_usage_events_table, demo_summary_and_talk_track_support_tickets_table, demo_summary_and_talk_track_churn_events_table [EXTRACTED 1.00]
- **TruLens LangGraph & Snowflake Evaluation Stack** — requirements_trulens_core, requirements_trulens_apps_langgraph, requirements_trulens_providers_cortex, requirements_trulens_connectors_snowflake [EXTRACTED 1.00]
- **Supervisor Conditional Routing** — final_architecture_supervisor, final_architecture_content_agent, final_architecture_data_analyst_agent, final_architecture_research_agent, final_architecture_end [EXTRACTED 1.00]
- **Supervisor Multi-Agent Architecture** — final_architecture_start, final_architecture_supervisor, final_architecture_content_agent, final_architecture_data_analyst_agent, final_architecture_research_agent, final_architecture_end [EXTRACTED 1.00]

## Communities (12 total, 4 thin omitted)

### Community 0 - "Synthetic Data Generation"
Cohesion: 0.09
Nodes (13): create_tables(), generate_churn_events(), generate_customers_data(), generate_support_tickets(), generate_usage_events(), insert_churn_events_data(), insert_customers_data(), insert_support_tickets_data() (+5 more)

### Community 1 - "App Imports & Session Setup"
Cohesion: 0.10
Nodes (7): _build_trial_plan(), _create_snowflake_session(), get_latest_human_message(), has_plan(), is_plan_complete(), supervisor_node(), _synthesize_trial_response()

### Community 2 - "Snowflake Tables & Semantic Views"
Cohesion: 0.18
Nodes (16): CHURN_EVENTS Table, CONTENT_AGENT, Cortex Analyst REST API, CUSTOMER_BEHAVIOR_ANALYST Semantic View, CUSTOMER_INTELLIGENCE_DB.PUBLIC, CUSTOMERS Table, DATA_ANALYST_AGENT, RESEARCH_AGENT (+8 more)

### Community 3 - "LangGraph Supervisor & Agent Nodes"
Cohesion: 0.21
Nodes (7): build_query_with_context(), content_agent_node(), data_analyst_agent_node(), _execute_agent_step(), get_current_step(), research_agent_node(), State

### Community 4 - "Project Docs & Core Frameworks"
Cohesion: 0.15
Nodes (14): LangGraph, LangSmith Studio, Multi-Agent Customer Intelligence System, studio_app.py, Snowflake Developer Guide, LangGraph, Apache 2.0 LICENSE, SnowLangGraph Multi-Agent AI System (+6 more)

### Community 5 - "Cortex Analyst & SQL Helpers"
Cohesion: 0.18
Nodes (5): _extract_customer_ids(), _extract_keywords(), _format_rows_markdown(), _run_content_sql_search(), _run_cortex_analyst()

### Community 6 - "Architecture Diagram Components"
Cohesion: 0.53
Nodes (6): CONTENT_AGENT, DATA_ANALYST_AGENT, __end__, RESEARCH_AGENT, __start__, supervisor

### Community 7 - "LangGraph Studio Config"
Cohesion: 0.40
Nodes (4): dependencies, env, graphs, supervisor_workflow

## Knowledge Gaps
- **14 isolated node(s):** `sql/05_setup_cortex_agents.sql`, `LangGraph`, `Snowflake Cortex`, `Snowflake Developer Guide`, `Apache 2.0 LICENSE` (+9 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 60 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `studio_app.py` connect `Project Docs & Core Frameworks` to `Snowflake Tables & Semantic Views`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **What connects `sql/05_setup_cortex_agents.sql`, `LangGraph`, `Snowflake Cortex` to the rest of the system?**
  _14 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Synthetic Data Generation` be split into smaller, more focused modules?**
  _Cohesion score 0.09462365591397849 - nodes in this community are weakly interconnected._
- **Why does `State` connect `LangGraph Supervisor & Agent Nodes` to `App Imports & Session Setup`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Should `App Imports & Session Setup` be split into smaller, more focused modules?**
  _Cohesion score 0.09538461538461539 - nodes in this community are weakly interconnected._
- **Why does `get_latest_human_message()` connect `App Imports & Session Setup` to `LangGraph Supervisor & Agent Nodes`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._