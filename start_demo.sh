#!/usr/bin/env bash
# Start the SnowLangGraph demo stack, verify Snowflake + LangGraph live,
# open presenter browser tabs, and queue prompts for DEMO_SCRIPT.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${LANGGRAPH_PORT:-2024}"
HOST="127.0.0.1"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
LANGGRAPH_BIN="$SCRIPT_DIR/.venv/bin/langgraph"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/langgraph_dev.log"

RUN_TESTS=1
RUN_WARMUP=1
RUN_EVAL=0
OPEN_TABS=1
USE_CLIPBOARD=1
INTERACTIVE_MENU=1
FOLLOW_LOGS=0
RESTART_EXISTING=0
CHECK_ONLY=0

SERVER_PID=""
TAIL_PID=""
WARMUP_TRACE_ID=""
WARMUP_THREAD_ID=""

usage() {
    cat <<'EOF'
Usage: ./start_demo.sh [options]

Starts the LangGraph dev server, verifies Snowflake + LangGraph readiness,
opens the demo browser tabs, and queues the 5-act demo prompts onto your clipboard.

Options:
  --port PORT       Port for langgraph dev (default: 2024)
  --restart         Stop an existing listener on PORT before starting
  --skip-tests      Skip offline unit tests (tests/)
  --skip-warmup     Skip live Snowflake/LangGraph warmup run
  --with-eval       Run evaluate_observability.py before starting server
  --no-open         Do not open browser tabs
  --no-clipboard    Do not copy prompts to clipboard
  --follow-logs     Stream server logs instead of interactive cue stepper
  --check-only      Verify tests, server startup, and live warmup, then exit
  -h, --help        Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            [[ $# -ge 2 ]] || { echo "Missing value for --port" >&2; exit 2; }
            PORT="$2"
            shift 2
            ;;
        --restart)
            RESTART_EXISTING=1
            shift
            ;;
        --skip-tests)
            RUN_TESTS=0
            shift
            ;;
        --skip-warmup)
            RUN_WARMUP=0
            shift
            ;;
        --with-eval)
            RUN_EVAL=1
            shift
            ;;
        --no-open)
            OPEN_TABS=0
            shift
            ;;
        --no-clipboard)
            USE_CLIPBOARD=0
            shift
            ;;
        --follow-logs)
            FOLLOW_LOGS=1
            INTERACTIVE_MENU=0
            shift
            ;;
        --check-only)
            CHECK_ONLY=1
            OPEN_TABS=0
            USE_CLIPBOARD=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

DECK_FILE="$SCRIPT_DIR/DEMO_DECK.html"
STUDIO_URL="https://smith.langchain.com/studio/?baseUrl=http://${HOST}:${PORT}"
EXPERIMENTS_URL="https://smith.langchain.com/o/4c3b325e-7b1a-496e-9bb2-de5ce01d7097/datasets/0e556ec0-f5fc-4fb2-a1fc-725af7df428c/compare?selectedSessions=bf610abf-a4ab-4e0f-a5ca-b253b070e431"
SNOWSIGHT_URL="https://app.snowflake.com/"

cleanup() {
    trap - EXIT INT TERM
    if [[ -n "$TAIL_PID" ]] && kill -0 "$TAIL_PID" 2>/dev/null; then
        kill -TERM "$TAIL_PID" 2>/dev/null || true
        wait "$TAIL_PID" 2>/dev/null || true
    fi
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo ""
        echo "Stopping langgraph dev (PID $SERVER_PID)..."
        pkill -TERM -P "$SERVER_PID" 2>/dev/null || true
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        for _ in 1 2 3 4 5 6; do
            if ! kill -0 "$SERVER_PID" 2>/dev/null; then
                break
            fi
            sleep 0.5
        done
        if kill -0 "$SERVER_PID" 2>/dev/null; then
            pkill -KILL -P "$SERVER_PID" 2>/dev/null || true
            kill -KILL "$SERVER_PID" 2>/dev/null || true
        fi
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

step() {
    printf "\n==> %s\n" "$1"
}

copy_to_clipboard() {
    local text="$1"
    local label="$2"
    if [[ "$USE_CLIPBOARD" -eq 0 ]]; then
        return 0
    fi
    if command -v pbcopy >/dev/null 2>&1; then
        printf "%s" "$text" | pbcopy
        echo "Copied to clipboard: $label"
    elif command -v xclip >/dev/null 2>&1; then
        printf "%s" "$text" | xclip -selection clipboard
        echo "Copied to clipboard: $label"
    else
        echo "Clipboard helper not found; copy manually: $label"
    fi
}

check_prerequisites() {
    step "Checking environment"
    [[ -x "$PYTHON_BIN" ]] || { echo "Missing $PYTHON_BIN. Create .venv first (uv venv && uv pip install -r requirements.txt)." >&2; exit 1; }
    [[ -x "$LANGGRAPH_BIN" ]] || { echo "Missing $LANGGRAPH_BIN. Install requirements.txt in .venv." >&2; exit 1; }
    [[ -f "$SCRIPT_DIR/.env" ]] || { echo "Missing .env. Copy .env.template to .env and fill in Snowflake/LangSmith settings." >&2; exit 1; }
    [[ -f "$SCRIPT_DIR/langgraph.json" ]] || { echo "Missing langgraph.json in $SCRIPT_DIR." >&2; exit 1; }
    echo "Workspace: $SCRIPT_DIR"
}

run_offline_tests() {
    if [[ "$RUN_TESTS" -eq 0 ]]; then
        return 0
    fi
    step "Running offline unit tests"
    "$PYTHON_BIN" -m unittest discover -s tests -v
}

run_eval_suite() {
    if [[ "$RUN_EVAL" -eq 0 ]]; then
        return 0
    fi
    step "Running evaluate_observability.py"
    "$PYTHON_BIN" "$SCRIPT_DIR/evaluate_observability.py"
}

ensure_port_free() {
    local pids=""
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    fi

    if [[ -n "$pids" ]]; then
        if [[ "$RESTART_EXISTING" -eq 1 ]]; then
            step "Stopping existing listener on port $PORT ($pids)"
            local pid
            while IFS= read -r pid; do
                [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
            done <<< "$pids"
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                if [[ -z "$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)" ]]; then
                    break
                fi
                sleep 0.5
            done
            pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
            if [[ -n "$pids" ]]; then
                echo "Port $PORT did not free cleanly; force-stopping ($pids)"
                while IFS= read -r pid; do
                    [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
                done <<< "$pids"
                sleep 0.5
            fi
        else
            echo "Port $PORT is already in use (PID: $pids)." >&2
            echo "Re-run with --restart to replace it (avoids stale-terminal Errno 5 errors)." >&2
            exit 1
        fi
    elif curl -sf "http://${HOST}:${PORT}/ok" >/dev/null 2>&1; then
        echo "Server already responds on http://${HOST}:${PORT}/ok (use --restart or --port)." >&2
        exit 1
    fi
}

start_server() {
    ensure_port_free
    mkdir -p "$LOG_DIR"
    : >"$LOG_FILE"

    step "Starting LangGraph dev server on http://${HOST}:${PORT}"
    "$LANGGRAPH_BIN" dev --host "$HOST" --port "$PORT" --allow-blocking --no-browser --no-reload </dev/null >"$LOG_FILE" 2>&1 &
    SERVER_PID=$!

    local ready=0
    for _ in $(seq 1 60); do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo "langgraph dev exited unexpectedly. Recent logs:" >&2
            tail -n 40 "$LOG_FILE" >&2 || true
            exit 1
        fi
        if curl -sf "http://${HOST}:${PORT}/ok" 2>/dev/null | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
            ready=1
            break
        fi
        sleep 0.5
    done

    if [[ "$ready" -ne 1 ]]; then
        echo "Timed out waiting for http://${HOST}:${PORT}/ok. Recent logs:" >&2
        tail -n 40 "$LOG_FILE" >&2 || true
        exit 1
    fi
    echo "Server healthy: http://${HOST}:${PORT}/ok -> {\"ok\":true} (PID $SERVER_PID)"
    echo "Server logs: $LOG_FILE"
}

_warmup_python() {
    "$PYTHON_BIN" - "$HOST" "$PORT" <<'PY'
import json
import re
import sys
import urllib.error
import urllib.request

host, port = sys.argv[1], sys.argv[2]
base = f"http://{host}:{port}"
prompt = "Find customers complaining about API issues and analyze their churn rate and monthly revenue"

def post_json(path: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

try:
    thread = post_json("/threads", {"metadata": {"source": "start_demo_warmup"}}, timeout=30)
    thread_id = thread["thread_id"]
    result = post_json(
        f"/threads/{thread_id}/runs/wait",
        {
            "assistant_id": "supervisor_workflow",
            "input": {"messages": [{"role": "user", "content": prompt}]},
        },
        timeout=180,
    )
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    print(f"ERROR: HTTP {exc.code} during warmup: {body}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"ERROR: Warmup request failed: {exc}", file=sys.stderr)
    sys.exit(1)

errors = result.get("execution_errors") or []
if errors:
    print("ERROR: Live warmup returned execution_errors:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    if any("250001" in str(e) or "Network policy" in str(e) for e in errors):
        print(
            "\nFIX (Snowflake 250001): Attach a network policy to your Snowflake user or create/refresh a PAT with network-policy bypass and update SNOWFLAKE_PASSWORD in .env.",
            file=sys.stderr,
        )
    sys.exit(1)

sql_metrics = result.get("sql_metrics") or []
if not sql_metrics:
    print("ERROR: Warmup completed with 0 Snowflake queries executed.", file=sys.stderr)
    sys.exit(1)

messages = result.get("messages") or []
final_text = ""
if messages:
    last = messages[-1]
    final_text = last.get("content", "") if isinstance(last, dict) else str(last)

trace_match = re.search(r"LangSmith Trace ID\*\*:\s*\x60([^\x60]+)\x60", final_text)
trace_id = trace_match.group(1) if trace_match else ""
steps = [s.get("agent") for s in (result.get("plan") or {}).get("steps", [])]

print(f"THREAD_ID={thread_id}")
print(f"TRACE_ID={trace_id}")
print(f"AGENTS={','.join(steps)}")
print(f"QUERIES={len(sql_metrics)}")
PY
}

warmup_demo() {
    if [[ "$RUN_WARMUP" -eq 0 ]]; then
        return 0
    fi
    step "Warming Snowflake warehouse + verifying live server run"
    local warmup_out
    if ! warmup_out="$(_warmup_python)"; then
        echo "Warmup check failed. See errors above or $LOG_FILE." >&2
        exit 1
    fi

    WARMUP_THREAD_ID="$(printf "%s\n" "$warmup_out" | sed -n 's/^THREAD_ID=//p' | head -n 1)"
    WARMUP_TRACE_ID="$(printf "%s\n" "$warmup_out" | sed -n 's/^TRACE_ID=//p' | head -n 1)"
    local agents queries
    agents="$(printf "%s\n" "$warmup_out" | sed -n 's/^AGENTS=//p' | head -n 1)"
    queries="$(printf "%s\n" "$warmup_out" | sed -n 's/^QUERIES=//p' | head -n 1)"

    echo "Live warmup succeeded: route=[$agents], queries=$queries, thread=$WARMUP_THREAD_ID"
    if [[ -n "$WARMUP_TRACE_ID" && "$WARMUP_TRACE_ID" != "local-no-trace" ]]; then
        echo "Warmup LangSmith Trace ID: $WARMUP_TRACE_ID"
    else
        echo "Warmup LangSmith Trace ID: unavailable (check LANGSMITH_TRACING / LANGSMITH_API_KEY)"
    fi
}

open_browser_tabs() {
    if [[ "$OPEN_TABS" -eq 0 ]]; then
        return 0
    fi
    if ! command -v open >/dev/null 2>&1; then
        echo "Skipping browser launch ('open' command not found)."
        return 0
    fi
    step "Opening 4 demo browser tabs"
    if [[ -f "$DECK_FILE" ]]; then
        open "$DECK_FILE"
        sleep 0.4
    fi
    open "$STUDIO_URL"
    sleep 0.4
    open "$EXPERIMENTS_URL"
    sleep 0.4
    open "$SNOWSIGHT_URL"
    echo "0. Slide Deck:  $DECK_FILE"
    echo "1. Studio:      $STUDIO_URL"
    echo "2. Experiments: $EXPERIMENTS_URL"
    echo "3. Snowsight:   $SNOWSIGHT_URL"
}

build_chart_sql() {
    local trace_expr="'<paste_trace_id>'"
    if [[ -n "$WARMUP_TRACE_ID" && "$WARMUP_TRACE_ID" != "local-no-trace" ]]; then
        trace_expr="'$WARMUP_TRACE_ID'"
    fi
    cat <<EOF
-- One bar per agent step for one LangSmith trace
SELECT
    TO_VARCHAR(STEP_NUMBER) || ' - ' || AGENT_NAME AS STEP_LABEL,
    COUNT(*)            AS QUERIES,
    SUM(ELAPSED_MS)     AS TOTAL_MS,
    SUM(ROWS_PRODUCED)  AS ROWS_OUT
FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
WHERE LANGSMITH_TRACE_ID = $trace_expr
GROUP BY STEP_NUMBER, AGENT_NAME
ORDER BY STEP_NUMBER;
EOF
}

build_latest_chart_sql() {
    cat <<'EOF'
-- One bar per agent step for the newest LangSmith trace
SELECT
    TO_VARCHAR(STEP_NUMBER) || ' - ' || AGENT_NAME AS STEP_LABEL,
    COUNT(*)            AS QUERIES,
    SUM(ELAPSED_MS)     AS TOTAL_MS,
    SUM(ROWS_PRODUCED)  AS ROWS_OUT
FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
WHERE LANGSMITH_TRACE_ID = (
    SELECT LANGSMITH_TRACE_ID
    FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
    ORDER BY START_TIME DESC
    LIMIT 1
)
GROUP BY STEP_NUMBER, AGENT_NAME
ORDER BY STEP_NUMBER;
EOF
}

CUE_1_TITLE="Act 1 — Cold Open (Studio new thread)"
CUE_1_TEXT="Find customers complaining about API issues and analyze their churn rate and monthly revenue"

CUE_2_TITLE="Act 3 — Snowsight Chart SQL"
CUE_2_TEXT=""

CUE_3_TITLE="Act 4a — Single-Hop Research Query (Studio new thread)"
CUE_3_TEXT="What industries have the highest total MRR and average customer lifetime?"

CUE_4_TITLE="Act 4b — CLV Semantic View Story (Studio new thread)"
CUE_4_TEXT="What is the average customer lifetime value by industry?"

CUE_5_TITLE="Act 4B (Optional) — Intentional Loose-Search Trace (Studio new thread)"
CUE_5_TEXT="How many urgent API support tickets are there?"

print_cue_sheet() {
    step "Demo Queue Ready"
    cat <<EOF
URLs:
  Slide Deck:  $DECK_FILE
  Studio:      $STUDIO_URL
  Experiments: $EXPERIMENTS_URL
  Snowsight:   $SNOWSIGHT_URL

Queued Cues (from DEMO_SCRIPT.md):
  [1] $CUE_1_TITLE
      $CUE_1_TEXT
  [2] $CUE_2_TITLE
      (Copies Snowsight Bar Chart SQL pre-filled with warmup Trace ID; use [s] for newest live trace)
  [3] $CUE_3_TITLE
      $CUE_3_TEXT
  [4] $CUE_4_TITLE
      $CUE_4_TEXT
  [5] $CUE_5_TITLE
      $CUE_5_TEXT
EOF
}

copy_cue() {
    local idx="$1"
    case "$idx" in
        1) copy_to_clipboard "$CUE_1_TEXT" "$CUE_1_TITLE" ;;
        2) copy_to_clipboard "$CUE_2_TEXT" "$CUE_2_TITLE" ;;
        3) copy_to_clipboard "$CUE_3_TEXT" "$CUE_3_TITLE" ;;
        4) copy_to_clipboard "$CUE_4_TEXT" "$CUE_4_TITLE" ;;
        5) copy_to_clipboard "$CUE_5_TEXT" "$CUE_5_TITLE" ;;
        s|S) copy_to_clipboard "$(build_latest_chart_sql)" "Snowsight Chart SQL (newest trace auto-selected)" ;;
        *) echo "Unknown cue '$idx'. Choose 1-5, s, l, or q." ;;
    esac
}

run_interactive_queue() {
    local current=1
    local next=2
    echo ""
    echo "Controls: [Enter]=copy next cue ($next) | [1-5]=copy cue | [s]=latest-trace SQL | [l]=logs | [q]=quit"
    while true; do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo "langgraph dev stopped unexpectedly. Recent logs:" >&2
            tail -n 40 "$LOG_FILE" >&2 || true
            exit 1
        fi
        printf "\n[demo queued: #%d | Enter -> #%d] > " "$current" "$next"
        local choice=""
        if ! IFS= read -r choice; then
            break
        fi
        case "$choice" in
            "")
                copy_cue "$next"
                current="$next"
                next=$(( current < 5 ? current + 1 : 1 ))
                ;;
            1|2|3|4|5)
                copy_cue "$choice"
                current="$choice"
                next=$(( current < 5 ? current + 1 : 1 ))
                ;;
            s|S)
                copy_cue "s"
                ;;
            l|L)
                tail -n 30 "$LOG_FILE" || true
                ;;
            q|Q)
                break
                ;;
            *)
                echo "Enter=next cue, 1-5=specific cue, s=newest-trace SQL, l=server logs, q=quit"
                ;;
        esac
    done
}

check_prerequisites
run_offline_tests
run_eval_suite
start_server
warmup_demo

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    step "Check-only complete"
    echo "Server startup and live warmup succeeded; shutting down cleanly."
    exit 0
fi

CUE_2_TEXT="$(build_chart_sql)"
open_browser_tabs
print_cue_sheet
copy_cue 1

if [[ "$INTERACTIVE_MENU" -eq 1 && -t 0 && "$FOLLOW_LOGS" -eq 0 ]]; then
    run_interactive_queue
else
    echo ""
    echo "Streaming $LOG_FILE (Ctrl-C to stop server)..."
    tail -n 20 -f "$LOG_FILE" &
    TAIL_PID=$!
    wait "$SERVER_PID" || true
fi
