# Parked (not bugs, not done)

The list from 2026-11-17 is cleared except for the items below.

- **Managed mode is untested.** `SNOWFLAKE_USE_CORTEX_AGENTS=true` and `sql/05` `type: "function"` need a paid account. Decide before the demo if you speak about it.
- **Login may fail again with `250001`.** It failed once at 00:20 PST on 2026-11-17 and then recovered by itself. Cause unconfirmed. It means Snowflake asked for a network policy when the PAT logged in. Re-test the login right before the demo. If it fails, make a new PAT in Snowsight, or attach a network policy to your user.
- **GitNexus index** may go stale. Re-run `node .gitnexus/run.cjs analyze --index-only` when the code settles.- **Ticket search is too loose (kept on purpose, used in demo Act 4B).** `run_content_sql_search` in `snowflake_client.py` ORs all keywords and searches "urgent" in ticket text, so "How many urgent API support tickets are there?" returns 729 tickets, including a password reset. Do NOT fix it before the demo. After the demo: use AND logic or the priority column, and add a relevance evaluator.

Cleared on 2026-11-17: CLV metric added to both semantic views (applied live; tested), usage data backfilled (see `HANDOFF.md` section 8), login code now shared (`snowflake_client.get_session()` in `data_generation.py` and the notebook), Cortex Analyst HTTP error path fired live (HTTP 404 on a fake semantic view, clear message, caught by the supervisor).
