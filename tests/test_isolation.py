import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data
import research
import research_ui
import session_store
from demo import demo_data
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)


def widget(items, label):
    return next(item for item in items if item.label == label)


class SessionStoreTests(unittest.TestCase):
    def test_missing_key_ignores_owner_environment_and_never_fetches(self):
        fetch = Mock()
        with patch.dict(os.environ, {"PARLAY_API_KEY": "owner-sentinel", "OPENAI_API_KEY": "owner-openai-sentinel"}):
            for value in (None, "", "  ", "replace_with_your_private_key"):
                state = {"props_api_key": value}
                with self.assertRaises(data.DataError):
                    session_store.refresh_board(state, fetch)
                self.assertNotIn("last_props_attempt", state)
                transport = Mock()
                with self.assertRaises(data.DataError):
                    data.fetch_live_board(value, get=transport)
                transport.assert_not_called()
                with self.assertRaises(research.ResearchError):
                    research.ask_research("Who is out?", key=value, model=research.DEFAULT_MODEL, context={}, history=[], lookup=Mock(), post=transport)
                transport.assert_not_called()
        fetch.assert_not_called()

    def test_only_visitors_key_is_sent_and_boards_are_not_shared_or_saved(self):
        alice, bob = {"props_api_key": " alice-key "}, {"props_api_key": "bob-key"}
        boards = [{"rows": [{"player": "Alice private board"}]}, {"rows": [{"player": "Bob private board"}]}]
        fetch = Mock(side_effect=boards)
        with tempfile.TemporaryDirectory() as folder, patch.object(data, "DATA", Path(folder)), patch.dict(os.environ, {"PARLAY_API_KEY": "owner-sentinel"}):
            self.assertIs(session_store.refresh_board(alice, fetch), boards[0])
            self.assertIs(session_store.refresh_board(bob, fetch), boards[1])
            self.assertEqual([call.args[0] for call in fetch.call_args_list], ["alice-key", "bob-key"])
            self.assertEqual(alice["session_board"], boards[0])
            self.assertEqual(bob["session_board"], boards[1])
            self.assertEqual(list(Path(folder).rglob("*")), [])
            self.assertNotIn("alice-key", json.dumps(alice["session_board"]))

    def test_clearing_and_changing_keys_remove_private_state_but_preserve_cooldown(self):
        state = {"props_api_key": "alice-key", "research_api_key": "alice-openai", "last_props_attempt": data.timestamp(), "session_board": {}, "history": [], "history_key": "old", "history_meta": {}, "watchlist": ["id"], "props_table": {}, "research_messages": ["private"], "research_error": "old"}
        session_store.clear_board_data(state)
        self.assertEqual(set(state), {"props_api_key", "research_api_key", "last_props_attempt"})
        session_store.clear_session(state)
        self.assertEqual(set(state), {"last_props_attempt"})
        self.assertEqual(session_store.visitor_key(state), "")


class VisitorDashboardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [patch.object(data, "DATA", Path(self.folder.name)), patch.dict(os.environ, {"PARLAY_API_KEY": "owner-props-sentinel", "OPENAI_API_KEY": "owner-openai-sentinel"})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def app(self):
        at = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertFalse(at.exception)
        return at

    def test_no_startup_requests_or_owner_credentials_or_starter_source(self):
        with patch.object(session_store, "fetch_live_board") as fetch, patch.object(research_ui, "ask_research") as ask:
            at = self.app()
            self.assertTrue(widget(at.button, "↻ Refresh live props").disabled)
            self.assertEqual(widget(at.text_input, "ParlayAPI key").value, "")
            self.assertNotIn("Starter snapshot", widget(at.selectbox, "Board source").options)
            widget(at.selectbox, "Board source").select("Demo (synthetic)").run()
            self.assertEqual(widget(at.text_input, "OpenAI API key").value, "")
            self.assertTrue(at.chat_input[0].disabled)
            fetch.assert_not_called()
            ask.assert_not_called()

    def test_refresh_uses_visitor_key_only_on_click_and_key_change_clears_board(self):
        board, logs = demo_data(data.current_season())
        board = {**board, "source": "Mock ParlayAPI visitor board", "metadata": {"partial": False, "request_count": 2, "estimated_credits": 6}}
        with patch.object(session_store, "fetch_live_board", return_value=board) as fetch, patch.object(data, "load_history", return_value=(logs, {})):
            at = self.app()
            widget(at.text_input, "ParlayAPI key").set_value("visitor-only-key").run()
            fetch.assert_not_called()
            widget(at.button, "↻ Refresh live props").click().run()
            self.assertFalse(at.exception)
            fetch.assert_called_once_with("visitor-only-key")
            self.assertEqual(at.metric[0].value, "128")
            widget(at.multiselect, "Market types").set_value(["Points"]).run()
            at.run()
            self.assertEqual(fetch.call_count, 1)
            widget(at.button, "+ Add to watchlist").click().run()
            widget(at.text_input, "ParlayAPI key").set_value("another-visitor-key").run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.dataframe), 0)
            self.assertNotIn("session_board", at.session_state.filtered_state)
            self.assertNotIn("watchlist", at.session_state.filtered_state)
            widget(at.button, "↻ Refresh live props").click().run()
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(any("cooling down" in error.value for error in at.error))
            self.assertEqual(list(Path(self.folder.name).rglob("*")), [])

    def test_two_app_sessions_have_independent_keys_boards_watchlists_and_chat(self):
        alice = self.app()
        widget(alice.text_input, "ParlayAPI key").set_value("alice-props").run()
        widget(alice.selectbox, "Board source").select("Demo (synthetic)").run()
        widget(alice.text_input, "OpenAI API key").set_value("alice-openai").run()
        widget(alice.button, "+ Add to watchlist").click().run()
        alice.session_state["session_board"] = {"rows": [{"player": "private"}]}
        alice.session_state["research_messages"] = [{"role": "user", "content": "private alice conversation"}]
        bob = self.app()
        self.assertEqual(len(bob.dataframe), 0)
        self.assertEqual(widget(bob.text_input, "ParlayAPI key").value, "")
        widget(bob.selectbox, "Board source").select("Demo (synthetic)").run()
        self.assertEqual(widget(bob.text_input, "OpenAI API key").value, "")
        self.assertTrue(bob.chat_input[0].disabled)
        widget(bob.checkbox, "Watchlist only").check().run()
        self.assertEqual(bob.metric[0].value, "0")
        self.assertEqual(bob.session_state["research_messages"], [])
        self.assertEqual(alice.session_state["props_api_key"], "alice-props")
        self.assertEqual(len(alice.session_state["watchlist"]), 1)

    def test_clear_button_removes_keys_board_watchlist_chat_and_resets_screen(self):
        at = self.app()
        widget(at.text_input, "ParlayAPI key").set_value("visitor-props").run()
        widget(at.selectbox, "Board source").select("Demo (synthetic)").run()
        widget(at.text_input, "OpenAI API key").set_value("visitor-openai").run()
        widget(at.button, "+ Add to watchlist").click().run()
        at.session_state["session_board"] = {"rows": [{"player": "private"}]}
        at.session_state["research_messages"] = [{"role": "user", "content": "private conversation"}]
        widget(at.button, "Clear keys & session data").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(widget(at.text_input, "ParlayAPI key").value, "")
        self.assertEqual(widget(at.selectbox, "Board source").value, "Live / session board")
        self.assertTrue(widget(at.button, "↻ Refresh live props").disabled)
        self.assertEqual(len(at.dataframe), 0)
        for name in ("research_api_key", "session_board", "watchlist", "research_messages"):
            self.assertNotIn(name, at.session_state.filtered_state)


if __name__ == "__main__":
    unittest.main()
