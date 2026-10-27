import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data
import research_ui
from research import ResearchError
from demo import demo_data
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)


def widget(items, label):
    return next(item for item in items if item.label == label)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [patch.object(data, "DATA", Path(self.folder.name))]
        for item in self.patches:
            item.start()
        self.at = AppTest.from_file(str(APP), default_timeout=20).run()
        self.at.selectbox[0].select("Demo (synthetic)").run()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def test_platform_and_metric_filters(self):
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.metric[0].value, "128")
        widget(self.at.multiselect, "Platforms").set_value(["PrizePicks"]).run()
        self.assertEqual(self.at.metric[0].value, "64")
        widget(self.at.number_input, "Minimum season hit rate (%)").set_value(60).run()
        widget(self.at.number_input, "Minimum consecutive hits").set_value(2).run()
        frame = self.at.dataframe[0].value
        self.assertFalse(frame.empty)
        self.assertTrue(frame["Season hit %"].between(60, 100).all())
        self.assertTrue(frame["Hit streak"].ge(2).all())
        widget(self.at.checkbox, "Filter 5-game average").check().run()
        widget(self.at.number_input, "Min 5-game average").set_value(20.0).run()
        self.assertTrue(self.at.dataframe[0].value["L5 avg"].ge(20).all())
        widget(self.at.checkbox, "Filter season average").check().run()
        widget(self.at.number_input, "Min season average").set_value(20.0).run()
        self.assertTrue(self.at.dataframe[0].value["Season avg"].ge(20).all())
        self.assertFalse(self.at.exception)

    def test_empty_search_and_missing_history_segment(self):
        widget(self.at.text_input, "Search player or matchup").set_value("Nobody [literal]").run()
        self.assertEqual(self.at.metric[0].value, "0")
        self.assertFalse(self.at.exception)
        widget(self.at.text_input, "Search player or matchup").set_value("").run()
        widget(self.at.selectbox, "Season segment").select("Preseason").run()
        self.assertEqual(self.at.metric[2].value, "0")
        self.assertFalse(self.at.exception)

    def test_watchlist_is_in_session_only_and_filters(self):
        widget(self.at.button, "+ Add to watchlist").click().run()
        self.assertEqual(len(self.at.session_state["watchlist"]), 1)
        self.assertFalse((Path(self.folder.name) / "watchlist.json").exists())
        widget(self.at.checkbox, "Watchlist only").check().run()
        self.assertEqual(self.at.metric[0].value, "1")
        self.assertFalse(self.at.exception)

    def test_underdog_multiplier_and_prizepicks_offers_filter_independently(self):
        self.assertTrue(self.at.dataframe[0].value.loc[lambda frame: frame.Platform.eq("PrizePicks"), "Underdog multiplier"].isna().all())
        widget(self.at.checkbox, "Filter Underdog multiplier").check().run()
        widget(self.at.number_input, "Min Underdog multiplier (×)").set_value(1.1).run()
        widget(self.at.number_input, "Max Underdog multiplier (×)").set_value(1.2).run()
        frame = self.at.dataframe[0].value
        pp = frame[frame.Platform.eq("PrizePicks")]
        ud = frame[frame.Platform.eq("Underdog")]
        self.assertEqual(len(pp), 64)
        self.assertEqual(len(ud), 8)
        self.assertTrue(ud["Underdog multiplier"].between(1.1, 1.2).all())
        widget(self.at.multiselect, "PrizePicks offer types").set_value([]).run()
        self.assertEqual(self.at.metric[0].value, "8")
        self.assertTrue(self.at.dataframe[0].value.Platform.eq("Underdog").all())
        widget(self.at.checkbox, "Filter Underdog multiplier").uncheck().run()
        self.assertEqual(self.at.metric[0].value, "64")
        self.assertFalse(self.at.exception)

    def test_points_market_filter_excludes_miskeyed_fantasy_offer(self):
        board, logs = demo_data(data.current_season())
        ordinary = next(row for row in board["rows"] if row["bookmaker"] == "underdog" and row["market_key"] == "player_points")
        ordinary = {**ordinary, "market": "Points"}
        fantasy = {**ordinary, "market": "Fantasy Points", "line": 61.349998474121094, "native_identity": {"line_id": "fantasy", "entry_stable_id": "native|61.35"}}
        board["rows"] = [ordinary, fantasy]
        self.at.session_state["session_board"] = board
        with patch.object(data, "load_history", return_value=(logs, {})):
            widget(self.at.selectbox, "Board source").select("Live / session board").run()
            self.assertFalse(self.at.exception)
            frame = self.at.dataframe[0].value
            self.assertEqual(set(frame.Market), {"Points", "Fantasy score"})
            self.assertTrue(frame.loc[frame.Market.eq("Fantasy score"), "Line"].eq(61.35).all())
            widget(self.at.multiselect, "Market types").set_value(["Points"]).run()
            frame = self.at.dataframe[0].value
            self.assertEqual(len(frame), 2)
            self.assertTrue(frame.Market.eq("Points").all())
            self.assertTrue(frame.Line.eq(ordinary["line"]).all())

    def test_chat_submission_citations_and_rerenders_do_not_repeat_requests(self):
        self.assertTrue(self.at.chat_input[0].disabled)
        result = {"content": "Check the dated report. [NBA](https://www.nba.com/injury-report)", "sources": [{"title": "NBA", "url": "https://www.nba.com/injury-report"}], "searched_at": "2026-10-26T23:00:00Z", "model": "gpt-5.4-mini", "search_calls": 1, "usage": {"input_tokens": 100, "output_tokens": 50}}
        with patch.object(research_ui, "ask_research", return_value=result) as ask:
            widget(self.at.text_input, "OpenAI API key").set_value("test-key-not-real").run()
            self.assertFalse(self.at.chat_input[0].disabled)
            self.at.chat_input[0].set_value("Why is this line high?").run()
            self.assertFalse(self.at.exception)
            self.assertEqual(ask.call_count, 1)
            call = ask.call_args
            self.assertEqual(call.args[0], "Why is this line high?")
            self.assertIsNotNone(call.kwargs["context"]["selected_prop"])
            history = self.at.session_state["research_messages"]
            self.assertEqual(len(history), 2)
            self.assertIn("[NBA]", history[1]["content"])
            self.assertNotIn("test-key-not-real", str(history))
            widget(self.at.multiselect, "Market types").set_value(["Points"]).run()
            self.assertEqual(ask.call_count, 1)
            widget(self.at.button, "Clear research chat").click().run()
            self.assertEqual(self.at.session_state["research_messages"], [])

    def test_chat_errors_do_not_render_an_uncited_assistant_answer(self):
        widget(self.at.text_input, "OpenAI API key").set_value("test-key-not-real").run()
        with patch.object(research_ui, "ask_research", side_effect=ResearchError("Search did not complete.")) as ask:
            self.at.chat_input[0].set_value("Is Curry out?").run()
            self.assertFalse(self.at.exception)
            self.assertEqual(len(self.at.session_state["research_messages"]), 1)
            self.assertEqual(self.at.session_state["research_messages"][0]["role"], "user")
            self.assertEqual(self.at.session_state["research_error"], "Search did not complete.")
            self.at.run()
            self.assertEqual(ask.call_count, 1)


if __name__ == "__main__":
    unittest.main()
