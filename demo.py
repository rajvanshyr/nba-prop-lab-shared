"""Deterministic, explicitly synthetic data for offline exploration and UI tests."""
import random
from datetime import datetime, timedelta, timezone

import pandas as pd

from metrics import prepare_logs


def demo_data(season: str):
    rng = random.Random(82)
    players = [("Jayson Tatum", "BOS", "MIL", 28, 8, 5), ("Nikola Jokic", "DEN", "LAL", 27, 12, 10), ("Shai Gilgeous-Alexander", "OKC", "DAL", 31, 5, 6), ("Luka Doncic", "LAL", "DEN", 30, 8, 9), ("Anthony Edwards", "MIN", "SAC", 28, 6, 5), ("Victor Wembanyama", "SAS", "HOU", 25, 11, 4), ("Jalen Brunson", "NYK", "CLE", 27, 3, 7), ("Cade Cunningham", "DET", "CHI", 26, 6, 9)]
    # Historical fixtures end before the current slate, even for past selected seasons.
    now = datetime.now(timezone.utc)
    end = min(now - timedelta(days=2), datetime(int(season[:4]) + 1, 4, 12, tzinfo=timezone.utc))
    rows, logs = [], []
    for index, (player, team, opponent, pts, reb, ast) in enumerate(players):
        for game in range(24):
            logs.append({"PLAYER_NAME": player, "PLAYER_ID": str(index + 1), "GAME_ID": f"demo-{index}-{game}", "GAME_DATE": (end - timedelta(days=game * 2)).isoformat(), "SEASON": season, "SEASON_TYPE": "Regular Season", "TEAM": team, "MATCHUP": f"vs {opponent}", "MIN": rng.randint(30, 39), "PTS": max(0, round(rng.gauss(pts, 6))), "REB": max(0, round(rng.gauss(reb, 3))), "AST": max(0, round(rng.gauss(ast, 2))), "FG3M": rng.randint(0, 5), "STL": rng.randint(0, 3), "BLK": rng.randint(0, 3), "TOV": rng.randint(0, 5)})
        for book in ("prizepicks", "underdog"):
            for market_index, (market, base) in enumerate((("player_points", pts), ("player_rebounds", reb), ("player_assists", ast), ("player_points_rebounds_assists", pts + reb + ast))):
                line = base - 2.5 + (1 if book == "underdog" else 0)
                row = {"bookmaker": book, "player": player, "market_key": market, "line": line, "period": "FULL", "odds_type": "standard", "available_sides": ["over", "under"], "commence_time": (now + timedelta(hours=4 + index)).isoformat(), "home_team": team, "away_team": opponent, "last_observed": now.isoformat(), "_received_at": now.isoformat(), "age_seconds": 0, "canonical_event_id": f"demo-{index}"}
                if book == "underdog":
                    higher, lower = ((1.18, 0.8), (1.0, 1.0), (0.91, 1.05), (2.05, 0.65))[(index + market_index) % 4]
                    row.update(selection_by_side={"over": {"payout_multiplier": higher}, "under": {"payout_multiplier": lower}}, payout_multiplier_scope="entry")
                rows.append(row)
    board = {"rows": rows, "fetched_at": now.isoformat(), "source": "DEMO · synthetic props and results", "metadata": {"partial": False, "scope": "Synthetic preview only"}}
    return board, prepare_logs(pd.DataFrame(logs))
