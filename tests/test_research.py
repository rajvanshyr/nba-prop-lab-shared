import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import demo_data
from metrics import analyze, normalize_props
from research import ResearchError, ask_research, build_context, lookup_props, render_response, safe_url


def searched():
    return {"type": "web_search_call", "status": "completed", "action": {"type": "search", "query": "NBA injury report"}}


def answer(text="The role needs verification. [cite]", **kwargs):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": [{"type": "url_citation", "url": "https://www.nba.com/injury-report", "title": "NBA injury report", "start_index": text.index("[cite]"), "end_index": text.index("[cite]") + 6, **kwargs}]}]}


class Response:
    def __init__(self, output=None, status=200, completion="completed"):
        self.status_code = status
        self.body = {"status": completion, "output": output or [], "usage": {"input_tokens": 100, "output_tokens": 50}}

    def json(self):
        return self.body


class ResearchTests(unittest.TestCase):
    def options(self, post, **kwargs):
        return dict(key="test-key-not-real", model="gpt-5.4-mini", context={"selected_prop": None}, history=[], lookup=Mock(return_value={"rows": []}), post=post, **kwargs)

    def test_request_requires_search_preserves_secrets_and_disables_storage(self):
        post = Mock(return_value=Response([searched(), answer()]))
        result = ask_research("Why is this line high?", **self.options(post))
        kwargs = post.call_args.kwargs
        payload = kwargs["json"]
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(payload["tool_choice"], {"type": "web_search"})
        self.assertFalse(payload["store"])
        self.assertTrue(payload["tools"][0]["external_web_access"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("test-key-not-real", json.dumps(payload))
        self.assertIn("[NBA injury report](https://www.nba.com/injury-report)", result["content"])
        self.assertNotIn("[cite]", result["content"])
        self.assertEqual(result["search_calls"], 1)

    def test_model_can_lookup_another_player_and_continue_statelessly(self):
        outputs = [Response([searched(), {"type": "reasoning", "id": "reasoning_1", "encrypted_content": "opaque"}, {"type": "function_call", "call_id": "call_1", "name": "lookup_props", "arguments": '{"player":"Yaxel","market":"Points"}'}]), Response([answer()])]
        calls = []
        def post(url, **kwargs):
            calls.append(copy.deepcopy(kwargs["json"]))
            return outputs.pop(0)
        options = self.options(post)
        result = ask_research("What about Yaxel?", **options)
        options["lookup"].assert_called_once_with("Yaxel", "Points")
        self.assertEqual(result["board_lookups"], 1)
        self.assertIn({"type": "reasoning", "id": "reasoning_1", "encrypted_content": "opaque"}, calls[1]["input"])
        self.assertTrue(any(item.get("type") == "function_call_output" for item in calls[1]["input"]))

    def test_uncited_or_unsearched_answers_are_not_returned(self):
        uncited = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "An unsupported injury claim.", "annotations": []}]}
        for output in ([searched(), uncited], [answer()]):
            with self.subTest(output=output):
                with self.assertRaises(ResearchError):
                    ask_research("Who is out?", **self.options(Mock(return_value=Response(output))))

    def test_auth_network_and_incomplete_errors_are_redacted_and_not_retried(self):
        for post in (Mock(return_value=Response(status=401)), Mock(side_effect=requests.ReadTimeout("private-key-url")), Mock(return_value=Response([searched()], completion="incomplete"))):
            with self.assertRaises(ResearchError) as error:
                ask_research("Who is out?", **self.options(post))
            self.assertNotIn("private-key-url", str(error.exception))
            self.assertNotIn("test-key-not-real", str(error.exception))
            self.assertEqual(post.call_count, 1)

    def test_missing_key_never_calls_openai(self):
        post = Mock()
        options = self.options(post)
        options["key"] = ""
        with self.assertRaisesRegex(ResearchError, "API key"):
            ask_research("Who is out?", **options)
        post.assert_not_called()

    def test_search_budget_is_not_reset_after_board_lookup(self):
        calls = []
        def post(url, **kwargs):
            calls.append(copy.deepcopy(kwargs["json"]))
            if len(calls) == 1:
                return Response([searched(), {"type": "function_call", "call_id": "call_1", "name": "lookup_props", "arguments": '{"player":"Yaxel","market":null}'}])
            return Response([answer()])
        result = ask_research("Who is out?", **self.options(post, max_searches=1))
        self.assertTrue(all(tool["type"] != "web_search" for tool in calls[1]["tools"]))
        self.assertEqual(result["search_calls"], 1)

    def test_lookup_rounds_are_bounded(self):
        post = Mock(return_value=Response([searched(), {"type": "function_call", "call_id": "call_1", "name": "lookup_props", "arguments": '{"player":"Yaxel","market":null}'}]))
        with self.assertRaisesRegex(ResearchError, "request limit"):
            ask_research("Who is out?", **self.options(post))
        self.assertEqual(post.call_count, 3)

    def test_unsafe_citation_urls_are_rejected_and_offset_fallback_is_clickable(self):
        for url in ("javascript:alert(1)", "file:///private/key", "https://user:password@example.com", "https://example.com/\nsecret"):
            self.assertIsNone(safe_url(url))
        text, sources = render_response([answer(start_index=10000, end_index=20000)])
        self.assertIn("[NBA injury report](https://www.nba.com/injury-report)", text)
        self.assertEqual(len(sources), 1)

    def test_citation_offsets_support_unicode_and_multiple_links(self):
        item = answer("Curry’s role is uncertain. [cite]")
        citation = dict(item["content"][0]["annotations"][0])
        citation.update(url="https://www.espn.com/nba/", title="ESPN")
        item["content"][0]["annotations"].append(citation)
        text, sources = render_response([item])
        self.assertIn("Curry’s", text)
        self.assertIn("[ESPN](https://www.espn.com/nba/)", text)
        self.assertNotIn("[cite]", text)
        self.assertEqual(len(sources), 2)

    def test_context_and_lookup_are_bounded_and_json_serializable(self):
        board, logs = demo_data("2026-27")
        frame = analyze(normalize_props(board["rows"]), logs, "2026-27", "Regular Season")
        context = build_context(frame.iloc[0], frame, logs, "2026-27", "Regular Season", board["source"], board["fetched_at"])
        json.dumps(context, allow_nan=False)
        found = lookup_props(frame, logs, "2026-27", "Regular Season", "Nikola", "Points")
        self.assertEqual(found["matching_players"], ["Nikola Jokic"])
        self.assertTrue(all(row["Player"] == "Nikola Jokic" and row["Market"] == "Points" for row in found["rows"]))
        self.assertLessEqual(len(found["rows"]), 40)
        self.assertLessEqual(len(context["selected_prop_recent_games"]["games"]), 10)
        json.dumps(found, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
