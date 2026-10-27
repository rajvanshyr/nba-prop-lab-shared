import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metrics import analyze, filter_board, normalize_props, prepare_logs

NOW = "2026-10-26T20:00:00Z"


def prop(**kwargs):
    return {"bookmaker": "underdog", "player": "Test Player Jr.", "market_key": "player_points", "period": "FULL", "line": 20, "available_sides": ["over", "under"], "commence_time": "2026-10-27T00:00:00Z", "last_observed": NOW, **kwargs}


def logs(values=(25, 24, 20, 19, 28, 10), **kwargs):
    rows = [{"PLAYER_NAME": "Test Player Jr.", "PLAYER_ID": "123", "GAME_ID": str(i), "GAME_DATE": f"2026-10-{25-i:02}T00:00:00Z", "SEASON": "2026-27", "SEASON_TYPE": "Regular Season", "MIN": 30, "PTS": v, "REB": 3, "AST": 4, **kwargs} for i, v in enumerate(values)]
    return prepare_logs(pd.DataFrame(rows))


class MetricTests(unittest.TestCase):
    def test_higher_lower_push_and_current_streak(self):
        board = analyze(normalize_props([prop()]), logs(), "2026-27", "Regular Season", NOW).set_index("Side")
        self.assertAlmostEqual(board.loc["Higher", "L5 avg"], 23.2)
        self.assertAlmostEqual(board.loc["Higher", "Season avg"], 21)
        self.assertEqual(board.loc["Higher", "Hit streak"], 2)
        self.assertEqual(board.loc["Lower", "Hit streak"], 0)
        self.assertEqual(board.loc["Higher", "Season hit %"], 60)
        self.assertEqual(board.loc["Lower", "Season hit %"], 40)
        self.assertEqual(board.loc["Higher", "Pushes"], 1)
        self.assertEqual(board.loc["Higher", "Games"], 6)

    def test_latest_push_breaks_streak_and_all_push_has_no_rate(self):
        board = analyze(normalize_props([prop()]), logs((20, 20)), "2026-27", "Regular Season", NOW)
        self.assertTrue(board["Season hit %"].isna().all())
        self.assertTrue(board["Hit streak"].eq(0).all())

    def test_season_dnp_and_future_excluded(self):
        frame = pd.concat([logs((10, 15)), logs((99,), MIN=0), logs((98,), SEASON="2025-26"), logs((97,), SEASON_TYPE="Preseason"), logs((96,), GAME_DATE="2026-10-28T00:00:00Z")])
        # Keep distinct identities so sample preparation cannot mask the test.
        frame["GAME_ID"] = [str(i) for i in range(len(frame))]
        board = analyze(normalize_props([prop()]), frame, "2026-27", "Regular Season", NOW)
        self.assertTrue(board.Games.eq(2).all())
        self.assertTrue(board["L5 avg"].eq(12.5).all())

    def test_combinations_aggregate_each_game(self):
        board = analyze(normalize_props([prop(market_key="player_points_rebounds_assists")]), logs((25, 24)), "2026-27", "Regular Season", NOW)
        self.assertTrue(board["Season avg"].eq(31.5).all())

    def test_zero_is_a_real_result_missing_component_is_not_zero(self):
        board = analyze(normalize_props([prop()]), logs((0,)), "2026-27", "Regular Season", NOW)
        self.assertEqual(board.iloc[0]["Season avg"], 0)
        frame = logs((25, 24))
        frame.loc[1, "AST"] = None
        board = analyze(normalize_props([prop(market_key="player_points_assists")]), frame, "2026-27", "Regular Season", NOW)
        self.assertTrue(board["Season avg"].isna().all())

    def test_fantasy_score_and_double_double(self):
        frame = logs((20,), REB=10, AST=5, STL=1, BLK=2, TOV=3)
        board = analyze(normalize_props([prop(market_key="player_fantasy_points", line=45.5)]), frame, "2026-27", "Regular Season", NOW)
        self.assertEqual(board.iloc[0]["Season avg"], 45.5)
        self.assertTrue(board.Pushes.eq(1).all())
        board = analyze(normalize_props([prop(market_key="player_double_double", line=0.5)]), frame, "2026-27", "Regular Season", NOW)
        self.assertTrue(board["Season avg"].eq(1).all())
        board = analyze(normalize_props([prop(market_key="player_triple_double", line=0.5)]), frame, "2026-27", "Regular Season", NOW)
        self.assertTrue(board["Season avg"].eq(0).all())

    def test_underdog_fantasy_line_is_never_compared_with_points_scored(self):
        # Real shape of the reported Jokic bug: both rows use player_points,
        # but one native label is Fantasy Points and its threshold is 61.35.
        rows = [
            prop(player="Nikola Jokic", market="Points", line=26.5, native_identity={"line_id": "pts"}),
            prop(player="Nikola Jokic", market="Fantasy Points", line=61.349998474121094, native_identity={"line_id": "fantasy", "entry_stable_id": "native|61.35"}),
        ]
        frame = logs((24,), PLAYER_NAME="Nikola Jokic", REB=20, AST=12, STL=1, BLK=2, TOV=3)
        board = analyze(normalize_props(rows), frame, "2026-27", "Regular Season", NOW)
        points = board[board.Market.eq("Points")]
        fantasy = board[board.Market.eq("Fantasy score")].set_index("Side")
        self.assertEqual(len(points), 2)
        self.assertTrue(points.Line.eq(26.5).all())
        self.assertTrue(fantasy.Line.eq(61.35).all())
        self.assertTrue(fantasy["Season avg"].eq(72).all())
        self.assertEqual(fantasy.loc["Lower", "Season hit %"], 0)
        self.assertEqual(fantasy.loc["Higher", "Season hit %"], 100)
        self.assertEqual(fantasy.loc["Lower", "Hit streak"], 0)

    def test_underdog_blocks_steals_label_repairs_wrong_steals_key(self):
        rows = [prop(market_key="player_steals", market="Blocks + Steals", line=1.5)]
        frame = logs((24,), STL=1, BLK=2)
        board = analyze(normalize_props(rows), frame, "2026-27", "Regular Season", NOW)
        self.assertTrue(board.Market.eq("Blocks + steals").all())
        self.assertTrue(board["Season avg"].eq(3).all())

    def test_unresolved_market_conflict_cannot_reuse_verified_points_history(self):
        for label in ("Rebounds", "Points in first five minutes"):
            with self.subTest(label=label):
                rows = [prop(market="Points", canonical_event_id="good"), prop(market=label, canonical_event_id="bad")]
                board = analyze(normalize_props(rows), logs(), "2026-27", "Regular Season", NOW)
                bad = board[board["Event ID"].eq("bad")]
                good = board[board["Event ID"].eq("good")]
                self.assertTrue(bad["Season avg"].isna().all())
                self.assertTrue(bad["Season hit %"].isna().all())
                self.assertTrue(bad.Market.str.startswith("Unverified:").all())
                self.assertTrue(good["Season avg"].notna().all())

    def test_native_combo_label_does_not_receive_single_player_statistics(self):
        board = analyze(normalize_props([prop(market="Points (Combo)")]), logs(), "2026-27", "Regular Season", NOW)
        self.assertTrue(board["Season hit %"].isna().all())
        self.assertTrue(board.Market.eq("Points (Combo)").all())

    def test_unknown_market_period_and_name_suffix(self):
        for raw in (prop(period="1H"), prop(market_key="player_fantasy_score"), prop(player="Test Player")):
            board = analyze(normalize_props([raw]), logs(), "2026-27", "Regular Season", NOW)
            self.assertTrue(board["Season avg"].isna().all())
            self.assertEqual(len(board), 2)

    def test_inferred_pp_types_explicit_empty_and_unknown_underdog(self):
        rows = [prop(bookmaker="prizepicks", odds_type="demon", available_sides=None), prop(bookmaker="prizepicks", odds_type="standard", available_sides=None), prop(available_sides=[]), prop(available_sides=None)]
        board = normalize_props(rows)
        self.assertEqual(len(board), 4)
        self.assertEqual(board.loc[board.Offer.eq("Demon"), "Side"].tolist(), ["Higher"])
        unknown = board[board.Platform.eq("Underdog")]
        self.assertEqual(unknown.Side.tolist(), ["Unspecified"])
        result = analyze(unknown, logs(), "2026-27", "Regular Season", NOW)
        self.assertTrue(result["Season avg"].notna().all())
        self.assertTrue(result["Season hit %"].isna().all())

    def test_event_and_line_identity_preserved(self):
        board = normalize_props([prop(canonical_event_id="a"), prop(canonical_event_id="a"), prop(canonical_event_id="b"), prop(canonical_event_id="a", line=21)])
        self.assertEqual(len(board), 6)

    def test_native_multiplier_matches_side_and_never_fills_other_platform(self):
        selection = {"over": {"payout_multiplier": 1.18}, "under": {"payout_multiplier": 0.8}}
        board = normalize_props([prop(selection_by_side=selection, payout_multiplier_scope="entry")]).set_index("Side")
        self.assertEqual(board.loc["Higher", "Underdog multiplier"], 1.18)
        self.assertEqual(board.loc["Lower", "Underdog multiplier"], 0.8)
        pp = normalize_props([prop(bookmaker="prizepicks", selection_by_side=selection, payout_multiplier_scope="entry")])
        self.assertTrue(pp["Underdog multiplier"].isna().all())

    def test_missing_invalid_and_unverified_multipliers_stay_blank(self):
        for value in (None, True, 0, -1, "not-a-number", "inf", "nan"):
            with self.subTest(value=value):
                board = normalize_props([prop(selection_by_side={"over": {"payout_multiplier": value}}, payout_multiplier_scope="entry")])
                self.assertTrue(board["Underdog multiplier"].isna().all())
        for scope in (None, "unknown", "odds"):
            board = normalize_props([prop(selection_by_side={"over": {"payout_multiplier": 1.18}}, payout_multiplier_scope=scope)])
            self.assertTrue(board["Underdog multiplier"].isna().all())
        board = normalize_props([prop(selection_by_side={"over": {"payout_multiplier": 1.18}}, payout_multiplier_scope="entry")]).set_index("Side")
        self.assertTrue(pd.isna(board.loc["Lower", "Underdog multiplier"]))

    def test_multiplier_can_be_loaded_from_csv_json(self):
        board = normalize_props([prop(selection_by_side='{"over":{"payout_multiplier":"1.18"},"under":{"payout_multiplier":0.8}}', payout_multiplier_scope="entry")]).set_index("Side")
        self.assertEqual(board.loc["Higher", "Underdog multiplier"], 1.18)
        self.assertEqual(board.loc["Lower", "Underdog multiplier"], 0.8)

    def test_platform_specific_filters_compose_and_exclude_unknown_multipliers(self):
        raw = [prop(bookmaker="prizepicks", odds_type="standard"), prop(bookmaker="prizepicks", odds_type="goblin", available_sides=["over"]), prop(selection_by_side={"over": {"payout_multiplier": 1.18}, "under": {"payout_multiplier": 0.8}}, payout_multiplier_scope="entry"), prop(player="No multiplier")]
        stats = analyze(normalize_props(raw), logs(), "2026-27", "Regular Season", NOW)
        defaults = dict(platforms=stats.Platform.unique(), markets=stats.Market.unique(), sides=stats.Side.unique())
        goblins = filter_board(stats, **defaults, offers=["Goblin"])
        self.assertEqual(len(goblins[goblins.Platform.eq("Underdog")]), 4)
        self.assertTrue(goblins[goblins.Platform.eq("PrizePicks")].Offer.eq("Goblin").all())
        filtered = filter_board(stats, **defaults, offers=["Goblin"], underdog_multiplier_range=(1.18, 1.18))
        self.assertEqual(len(filtered), 2)
        ud = filtered[filtered.Platform.eq("Underdog")]
        self.assertEqual(ud.Side.tolist(), ["Higher"])
        self.assertEqual(ud["Underdog multiplier"].tolist(), [1.18])
        ud_only = filter_board(stats, **defaults, offers=[], underdog_multiplier_range=(0.8, 1.18))
        self.assertEqual(len(ud_only), 2)
        self.assertTrue(ud_only.Platform.eq("Underdog").all())

    def test_metric_filters_and_literal_search(self):
        stats = analyze(normalize_props([prop(), prop(player="Missing [name]")]), logs(), "2026-27", "Regular Season", NOW)
        defaults = dict(platforms=stats.Platform.unique(), markets=stats.Market.unique(), sides=stats.Side.unique(), offers=stats.Offer.unique())
        result = filter_board(stats, **defaults, min_streak=2, hit_range=(50, 100), l5_range=(23, 24), season_range=(20, 22))
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0].Side, "Higher")
        self.assertEqual(len(filter_board(stats, **defaults, search="[name]")), 2)
        self.assertTrue(filter_board(stats, **{**defaults, "platforms": []}).empty)

    def test_freshness_is_observation_based_and_tipoff_nullable(self):
        rows = [prop(last_observed="2026-10-26T19:59:00Z"), prop(last_observed="2026-10-25T20:00:00Z", canonical_event_id="old"), prop(commence_time=None, canonical_event_id="unknown")]
        stats = analyze(normalize_props(rows), logs(), "2026-27", "Regular Season", NOW)
        defaults = dict(platforms=stats.Platform.unique(), markets=stats.Market.unique(), sides=stats.Side.unique(), offers=stats.Offer.unique())
        self.assertEqual(len(filter_board(stats, **defaults, fresh_only=True, upcoming_only=True, now=NOW)), 2)


if __name__ == "__main__":
    unittest.main()
