"""
Observability helpers: progress logging, LangSmith run annotation, LangSmith feedback.

The Snowflake QUERY_TAG is built in `snowflake_client.execute_snowflake_sql` from the trace id
that `get_trace_ids()` returns here.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langsmith import Client as LangSmithClient
from langsmith import get_current_run_tree

load_dotenv()

# Progress messages go through logging, not print(). A broken terminal can make print() raise
# OSError and crash a run. Logging swallows write errors.
logger = logging.getLogger("snowlanggraph")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


_langsmith_client: Optional[LangSmithClient] = None
if os.getenv("LANGSMITH_API_KEY"):
    try:
        _langsmith_client = LangSmithClient()
    except Exception:
        _langsmith_client = None


# =============================================================================
# LANGSMITH HELPERS
# =============================================================================


def get_trace_ids() -> tuple[Optional[str], Optional[str]]:
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


def annotate_run_tree(metadata: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None) -> None:
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


def emit_langsmith_feedback(run_id: Optional[str], scores: Dict[str, float], comment: str) -> None:
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
