import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data
import session_store


class Response:
    def __init__(self, body, headers=None, status=200):
        self._body = body
        self.headers = headers or {}
        self.status_code = status

    def json(self):
        return self._body


class AdapterTests(unittest.TestCase):
    def test_all_markets_per_book_pagination_and_headers(self):
        calls = []
        def get(url, **kwargs):
            calls.append(kwargs)
            return Response([{"player": "Example", "bookmaker": kwargs["params"]["bookmakers"]}], {"x-result-has-more": "true" if len(calls) == 1 else "false", "x-next-offset": "5000"})
        board = data.fetch_live_board("dummy-not-a-real-key", get)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("markets", calls[0]["params"])
        self.assertEqual(calls[1]["params"]["offset"], 5000)
        self.assertEqual(calls[-1]["params"]["bookmakers"], "underdog")
        self.assertFalse(board["metadata"]["partial"])
        self.assertNotIn("dummy-not-a-real-key", json.dumps(board))

    def test_missing_completeness_header_and_truncation_flag_partial(self):
        for headers in ({}, {"x-result-has-more": "false", "x-result-truncated": "true"}):
            board = data.fetch_live_board("dummy", lambda *a, **k: Response([{"player": "Example"}], headers))
            self.assertTrue(board["metadata"]["partial"])

    def test_empty_degraded_board_does_not_replace_saved_data(self):
        with self.assertRaisesRegex(data.DataError, "previous board was preserved"):
            data.fetch_live_board("dummy", lambda *a, **k: Response([], {"x-result-has-more": "false", "x-result-degraded": "prizepicks"}))

    def test_429_stops_without_retry_and_redacts_response(self):
        calls = []
        def get(*a, **kwargs):
            calls.append(1)
            return Response({"secret": "private"}, status=429)
        with self.assertRaises(data.DataError) as error:
            data.fetch_live_board("dummy", get)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private", str(error.exception))

    def test_failed_refresh_preserves_board_and_cooldown(self):
        state = {"props_api_key": "visitor-key", "session_board": {"sentinel": True}}
        with patch.object(session_store, "fetch_live_board", side_effect=data.DataError("Failed")) as fetch:
            with self.assertRaises(data.DataError):
                session_store.refresh_board(state)
            self.assertEqual(state["session_board"], {"sentinel": True})
            with self.assertRaisesRegex(data.DataError, "cooling down"):
                session_store.refresh_board(state)
            self.assertEqual(fetch.call_count, 1)

    def test_espn_completed_games_separate_segments_and_seasons(self):
        body = {"labels": ["MIN", "PTS", "REB", "AST", "3PT"], "events": {"a": {"gameResult": "W", "gameDate": "2026-10-24T00:00:00Z"}, "b": {"gameDate": "2026-10-27T00:00:00Z"}}, "seasonTypes": [{"displayName": "2026-27 Regular Season", "categories": [{"type": "event", "events": [{"eventId": "a", "stats": ["32:30", "0", "7", "8", "2-5"]}, {"eventId": "b", "stats": ["30", "20", "7", "8", "2-5"]}]}]}, {"displayName": "2025-26 Regular Season", "categories": [{"type": "event", "events": [{"eventId": "a", "stats": ["30", "50", "7", "8", "2-5"]}]}]}]}
        rows = data.parse_espn_log(body, "Example", "123", "2026-27")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["MIN"], 32.5)
        self.assertEqual(rows[0]["PTS"], 0)
        self.assertEqual(rows[0]["FG3M"], 2)
        self.assertEqual(rows[0]["FG3A"], 5)
        self.assertIsNone(rows[0]["STL"])

    def test_public_espn_cache_does_not_save_visitors_uploaded_player_label(self):
        import tempfile
        body = {"labels": ["PTS"], "events": {"a": {"gameResult": "W", "gameDate": "2026-10-24T00:00:00Z"}}, "seasonTypes": [{"displayName": "2026-27 Regular Season", "categories": [{"type": "event", "events": [{"eventId": "a", "stats": ["20"]}]}]}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(data, "DATA", Path(folder)), patch.object(data, "public_json", return_value=body) as get:
            rows, _ = data.player_logs("Private visitor label", "123", "2026-27")
            self.assertEqual(rows[0]["PLAYER_NAME"], "Private visitor label")
            saved = (Path(folder) / "logs" / "2026-27-123.json").read_text()
            self.assertNotIn("Private visitor label", saved)
            rows, _ = data.player_logs("Another visitor label", "123", "2026-27")
            self.assertEqual(rows[0]["PLAYER_NAME"], "Another visitor label")
            self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
