"""Offline unit tests: no Snowflake login, no network.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Keep the tests offline: no LangSmith traces or feedback. load_dotenv() never overrides these values.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_API_KEY"] = ""

# Any attempt to log in during import or tests fails the run loudly.
with mock.patch("snowflake.snowpark.Session.builder", new_callable=mock.PropertyMock) as _no_login:
    _no_login.side_effect = AssertionError("Snowflake login attempted at import time")
    import observability as obs  # noqa: E402
    import snowflake_client as sf  # noqa: E402
    import supervisor as sup  # noqa: E402
    import studio_app  # noqa: E402  (the Studio entry point must still export `app`)

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402


def agents(question: str):
    return [s["agent"] for s in sup._build_trial_plan(question)["steps"]]


class ImportIsOffline(unittest.TestCase):
    def test_tracing_is_off(self):
        from langsmith.utils import tracing_is_enabled

        self.assertFalse(tracing_is_enabled())
        self.assertIsNone(obs._langsmith_client)

    def test_no_session_created_at_import(self):
        self.assertIsNone(sf._session)
        self.assertTrue(hasattr(studio_app, "app"))


class PlannerRoutes(unittest.TestCase):
    # These four questions and routes are the evaluation set in evaluate_observability.py.
    def test_eval_routes(self):
        cases = {
            "What industries have the highest total MRR and average customer lifetime?": ["RESEARCH_AGENT"],
            "How many customers are in each plan type and what is their churn rate?": ["DATA_ANALYST_AGENT"],
            "Show customer feedback and support tickets about billing": ["CONTENT_AGENT"],
            "Find customers complaining about API issues and analyze their churn rate and monthly revenue": [
                "CONTENT_AGENT", "DATA_ANALYST_AGENT", "RESEARCH_AGENT",
            ],
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(agents(question), expected)

    def test_word_start_matching_not_substring(self):
        self.assertNotIn("CONTENT_AGENT", agents("rapid growth overview"))  # 'rapid' contains 'api'
        self.assertTrue(sup._build_trial_plan("inactive")["fallback"])  # no term matched: 'inactive' is not 'active...'
        self.assertIn("CONTENT_AGENT", agents("any API problems?"))
        self.assertIn("CONTENT_AGENT", agents("APIs are slow"))

    def test_unknown_question_falls_back_to_analyst(self):
        plan = sup._build_trial_plan("hello there")
        self.assertEqual([s["agent"] for s in plan["steps"]], ["DATA_ANALYST_AGENT"])
        self.assertTrue(plan["fallback"])
        self.assertEqual(plan["intents"], ["analyst"])

    def test_matched_question_is_not_a_fallback(self):
        plan = sup._build_trial_plan("churn rate by plan")
        self.assertFalse(plan["fallback"])
        self.assertEqual(plan["intents"], ["analyst"])
        self.assertIn("churn", plan["matched_terms"]["analyst"])

    def test_step_links(self):
        plan = sup._build_trial_plan("api tickets, churn and revenue")
        steps = plan["steps"]
        self.assertEqual([s["step_number"] for s in steps], [1, 2, 3])
        self.assertEqual(steps[0]["next_agent"], "DATA_ANALYST_AGENT")
        self.assertIsNone(steps[-1]["next_agent"])
        self.assertEqual(steps[1]["uses_data_from"], [1])
        self.assertEqual(steps[2]["uses_data_from"], [1, 2])
        self.assertEqual(plan["intents"], ["content", "analyst", "research"])

    def test_empty_question_has_no_crash(self):
        self.assertEqual(agents(""), ["DATA_ANALYST_AGENT"])


class NewTurn(unittest.TestCase):
    def test_empty(self):
        self.assertFalse(sup.is_new_turn([]))

    def test_human_last_is_new_turn(self):
        self.assertTrue(sup.is_new_turn([HumanMessage(content="hi")]))
        self.assertTrue(sup.is_new_turn([HumanMessage(content="a"), AIMessage(content="b"), HumanMessage(content="c")]))

    def test_agent_last_is_not_new_turn(self):
        self.assertFalse(sup.is_new_turn([HumanMessage(content="a"), AIMessage(content="b", name="CONTENT_AGENT")]))

    def test_dict_messages(self):
        self.assertTrue(sup.is_new_turn([{"role": "user", "content": "x"}]))
        self.assertTrue(sup.is_new_turn([{"type": "human", "content": "x"}]))
        self.assertFalse(sup.is_new_turn([{"role": "assistant", "content": "x"}]))


class MarkdownTable(unittest.TestCase):
    class Row:
        def __init__(self, d):
            self._d = d

        def as_dict(self):
            return self._d

    def test_pipe_and_newline_are_escaped(self):
        out = sf.format_rows_markdown([self.Row({"A": "x|y", "B": "line1\nline2"})])
        row = out.splitlines()[2]
        self.assertEqual(row, "| x\\|y | line1 line2 |")

    def test_cut_then_escape_keeps_row_shape(self):
        out = sf.format_rows_markdown([self.Row({"A": "|" * 200, "B": "ok"})])
        row = out.splitlines()[2]
        self.assertTrue(row.endswith("| ok |"))
        self.assertEqual(row.count("\\|"), 120)

    def test_empty(self):
        self.assertIn("No matching rows", sf.format_rows_markdown([]))


class SafeLogging(unittest.TestCase):
    def test_broken_stdout_does_not_crash_logging(self):
        class Broken:
            def write(self, _):
                raise OSError(5, "Input/output error")

            def flush(self):
                raise OSError(5, "Input/output error")

        handler = obs.logger.handlers[0]
        old = handler.stream
        handler.stream = Broken()
        try:
            with mock.patch("sys.stderr", Broken()):
                obs.logger.info("this must not raise")
        finally:
            handler.stream = old


class ManagedModeDoesNotHang(unittest.TestCase):
    def test_first_cortex_agent_call_creates_session_without_deadlock(self):
        import threading

        sf._cortex_agents.clear()
        fake_session = mock.Mock()
        done = {}

        def work():
            done["agent"] = sf.get_cortex_agent("CONTENT_AGENT")

        with mock.patch.object(sf, "_session", None), \
                mock.patch.object(sf, "_create_snowflake_session", return_value=fake_session), \
                mock.patch.object(sf, "SnowflakeCortexAgent") as fake_agent_cls:
            th = threading.Thread(target=work, daemon=True)
            th.start()
            th.join(timeout=5)
            self.assertFalse(th.is_alive(), "get_cortex_agent hung (lock deadlock)")
            self.assertIs(done["agent"], fake_agent_cls.return_value)
            self.assertIs(sf._session, fake_session)
        sf._cortex_agents.clear()


class QueryTagPerStatement(unittest.TestCase):
    def test_tag_is_sent_with_the_statement_not_set_on_the_session(self):
        fake_job = mock.Mock(query_id="QID1")
        fake_job.result.return_value = [object()]
        fake_df = mock.Mock()
        fake_df.collect.return_value = fake_job
        fake_session = mock.Mock()
        fake_session.sql.return_value = fake_df
        fake_session.query_tag = "untouched"
        with mock.patch.object(sf, "get_session", return_value=fake_session):
            rows, sfqid, _ = sf.execute_snowflake_sql("SELECT 1", agent_name="A", step_number=2, tool_name="T")
        self.assertEqual(sfqid, "QID1")
        self.assertEqual(fake_session.query_tag, "untouched")
        kwargs = fake_df.collect.call_args.kwargs
        self.assertEqual(kwargs["block"], False)
        import json

        tag = json.loads(kwargs["statement_params"]["QUERY_TAG"])
        self.assertEqual((tag["app"], tag["agent"], tag["step"], tag["tool"]), ("snowlanggraph", "A", 2, "T"))


if __name__ == "__main__":
    unittest.main()
