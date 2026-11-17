# Parked (not bugs, not done)

The list from 2026-11-16 is cleared (see `HANDOFF.md` section 7). What is left:

- **Usage data covers 20% of customers.** `data_generation.py` builds usage events for the first 2,000 customers (upstream behaviour). Fixing it means reloading the tables, which overwrites data and changes every number in the demo docs. Needs the owner's OK. It is written as a known limit in `DEMO_SUMMARY_AND_TALK_TRACK.md`.
- **Session code is copied in two more places.** `data_generation.py` and the notebook have their own login code. `snowflake_client._create_snowflake_session()` is the reference.
- **GitNexus index** may go stale. Re-run `node .gitnexus/run.cjs analyze --index-only` when the code settles.
- **Managed mode is untested.** `SNOWFLAKE_USE_CORTEX_AGENTS=true` and `sql/05` `type: "function"` need a paid account.
- **Cortex Analyst HTTP error path** never fired in a live run (covered only by reading the code).
- **Login may fail again with `250001`.** It failed once at 00:20 PST on 2026-11-17 and then recovered by itself. Cause unconfirmed. Re-test the login right before the demo.
