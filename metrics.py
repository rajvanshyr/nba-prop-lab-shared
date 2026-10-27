"""Pure prop normalization and historical statistics; no network or Streamlit."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone

import pandas as pd

BOOKS = {"prizepicks": "PrizePicks", "underdog": "Underdog"}
# Each combination is calculated per game before averaging or counting hits.
MARKETS = {
    "player_points": ("Points", {"PTS": 1}),
    "player_rebounds": ("Rebounds", {"REB": 1}),
    "player_assists": ("Assists", {"AST": 1}),
    "player_threes": ("3-pointers made", {"FG3M": 1}),
    "player_steals": ("Steals", {"STL": 1}),
    "player_blocks": ("Blocks", {"BLK": 1}),
    "player_turnovers": ("Turnovers", {"TOV": 1}),
    "player_blocks_steals": ("Blocks + steals", {"BLK": 1, "STL": 1}),
    "player_points_rebounds": ("Points + rebounds", {"PTS": 1, "REB": 1}),
    "player_points_assists": ("Points + assists", {"PTS": 1, "AST": 1}),
    "player_rebounds_assists": ("Rebounds + assists", {"REB": 1, "AST": 1}),
    "player_points_rebounds_assists": ("Points + rebounds + assists", {"PTS": 1, "REB": 1, "AST": 1}),
    "player_field_goals": ("Field goals made", {"FGM": 1}),
    "player_field_goals_attempted": ("Field goals attempted", {"FGA": 1}),
    "player_threes_attempted": ("3-pointers attempted", {"FG3A": 1}),
    "player_free_throws": ("Free throws made", {"FTM": 1}),
    "player_free_throws_attempted": ("Free throws attempted", {"FTA": 1}),
    "player_personal_fouls": ("Personal fouls", {"PF": 1}),
    "player_two_pointers_made": ("2-pointers made", {"FGM": 1, "FG3M": -1}),
    "player_two_pointers_attempted": ("2-pointers attempted", {"FGA": 1, "FG3A": -1}),
    # Both supported pick'em products publish these NBA fantasy-score weights.
    "player_fantasy_points": ("Fantasy score", {"PTS": 1, "REB": 1.2, "AST": 1.5, "STL": 3, "BLK": 3, "TOV": -1}),
    "player_double_double": ("Double-double", {"PTS": 1, "REB": 1, "AST": 1, "STL": 1, "BLK": 1}),
    "player_triple_double": ("Triple-double", {"PTS": 1, "REB": 1, "AST": 1, "STL": 1, "BLK": 1}),
}
ALIASES = {
    "player_three_pointers": "player_threes", "player_three_pointers_made": "player_threes",
    "player_3pt_made": "player_threes", "player_steals_blocks": "player_blocks_steals",
    "player_pts_rebs_asts": "player_points_rebounds_assists",
    "player_pts_rebs": "player_points_rebounds", "player_pts_asts": "player_points_assists",
    "player_rebs_asts": "player_rebounds_assists", "player_field_goals_made": "player_field_goals",
    "player_free_throws_made": "player_free_throws",
    "player_pra": "player_points_rebounds_assists",
    "player_blks_plus_stls": "player_blocks_steals",
    "player_fg_made": "player_field_goals",
    "player_fg_attempted": "player_field_goals_attempted",
    "player_3_pt_attempted": "player_threes_attempted",
}
STAT_COLUMNS = ["MIN", "PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGM", "FGA", "FG3A", "FTM", "FTA", "PF"]
LOG_COLUMNS = ["PLAYER_NAME", "PLAYER_ID", "GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE", "TEAM", "MATCHUP", *STAT_COLUMNS]
METRIC_COLUMNS = ["L5 avg", "L5 games", "Season avg", "Hit streak", "Season hit %", "Hits", "Misses", "Pushes", "Games", "L5 vs line"]


def name_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).casefold()
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)))


SOURCE_LABEL_MARKETS = {name_key(title): key for key, (title, _) in MARKETS.items()}
SOURCE_LABEL_MARKETS.update({
    "fantasypoints": "player_fantasy_points", "blockedshots": "player_blocks",
    "3ptmade": "player_threes", "3ptattempted": "player_threes_attempted",
    "fgmade": "player_field_goals", "fgattempted": "player_field_goals_attempted",
    "freethrowsmade": "player_free_throws", "ptsrebsasts": "player_points_rebounds_assists",
    "pra": "player_points_rebounds_assists", "ptsasts": "player_points_assists",
    "ptsrebs": "player_points_rebounds", "rebsasts": "player_rebounds_assists",
    "blksstls": "player_blocks_steals", "torecordadoubledouble": "player_double_double",
    "torecordatripledouble": "player_triple_double",
    "twopointersmade": "player_two_pointers_made",
    "twopointersattempted": "player_two_pointers_attempted",
})
# Observed provider taxonomy collisions. Only these explicit, source-labeled
# cases are repaired; other disagreements are kept visible without statistics.
SOURCE_KEY_CORRECTIONS = {
    ("underdog", "player_points", "player_fantasy_points"),
    ("underdog", "player_steals", "player_blocks_steals"),
}
INVALID_MARKET_STATUSES = {"Conflicting market metadata", "Unverified source label"}


def resolve_market(book: str, key: str, label: str, period: str) -> tuple[str, str]:
    if not label.strip():
        return key, "Source key only"
    label_key = name_key(label)
    if label_key.endswith("combo"):
        return (key if key.endswith("_combo") else key + "_combo"), "Multi-player combo"
    for prefix, expected_period in (("1q", "q1"), ("1h", "1h")):
        if label_key.startswith(prefix):
            if period != expected_period:
                return key, "Conflicting market metadata"
            label_key = label_key[len(prefix):]
    labeled_market = SOURCE_LABEL_MARKETS.get(label_key)
    base_key = re.sub(r"_(?:1st|2nd|3rd|4th)_(?:quarter|half)$", "", key)
    base_key = ALIASES.get(base_key, base_key)
    if labeled_market == base_key:
        return key, "Source label verified"
    if (book, key, labeled_market) in SOURCE_KEY_CORRECTIONS:
        return labeled_market, "Corrected source key using label"
    if labeled_market is not None:
        return key, "Conflicting market metadata"
    return key, "Unverified source label" if base_key in MARKETS else "Unsupported market"


def utc_time(value) -> pd.Timestamp:
    if value is None or value == "":
        return pd.NaT
    try:
        if isinstance(value, (int, float)):
            stamp = pd.Timestamp(value, unit="ms" if abs(value) > 1e11 else "s", tz="UTC")
        else:
            stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return pd.NaT
        return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    except (ValueError, TypeError, OverflowError):
        return pd.NaT


def normalize_props(rows: list[dict]) -> pd.DataFrame:
    """Keep unsupported offers visible; never infer a missing Underdog direction."""
    output = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        book = re.sub(r"[ _-]", "", str(raw.get("bookmaker", "")).lower())
        book = {"underdogfantasy": "underdog"}.get(book, book)
        if book not in BOOKS or raw.get("sport_key", "basketball_nba") != "basketball_nba":
            continue
        try:
            line = float(raw["line"])
        except (KeyError, ValueError, TypeError):
            continue
        player = str(raw.get("player") or "").strip()
        source_market_key = str(raw.get("market_key") or "").strip().lower()
        source_market = str(raw.get("market") or "").strip()
        market = source_market_key
        if not player or not market or not math.isfinite(line):
            continue
        market = ALIASES.get(market, market)
        period = re.sub(r"[ _-]", "", str(raw.get("period") or "").lower())
        period = "Full game" if period in {"full", "game", "fullgame", "fg", "0"} else period or "Unknown"
        market, market_status = resolve_market(book, market, source_market, period)
        offer_type = str(raw.get("odds_type") or raw.get("projection_type") or "unknown").lower()
        direction_map = {"over": "Higher", "more": "Higher", "higher": "Higher", "under": "Lower", "less": "Lower", "lower": "Lower"}
        supplied = raw.get("available_sides")
        if isinstance(supplied, str):
            try:
                supplied = json.loads(supplied)
            except ValueError:
                supplied = supplied.split(",")
        if isinstance(supplied, list):
            sides = sorted({direction_map[s.lower().strip()] for s in supplied if isinstance(s, str) and s.lower().strip() in direction_map})
            evidence = "Source-reported"
            if not sides:  # Explicitly empty means withdrawn, not an unknown two-way offer.
                continue
        elif book == "prizepicks" and offer_type in {"standard", "demon", "goblin"}:
            sides = ["Higher", "Lower"] if offer_type == "standard" else ["Higher"]
            evidence = "Projection type; verify in app"
        else:
            sides, evidence = ["Unspecified"], "Unknown"
        start = utc_time(raw.get("commence_time"))
        observed = utc_time(raw.get("last_observed") or raw.get("last_update"))
        injury = raw.get("injury") if isinstance(raw.get("injury"), dict) else {}
        event = str(raw.get("canonical_event_id") or raw.get("event_id") or raw.get("game_date") or "")
        native = raw.get("native_identity") if isinstance(raw.get("native_identity"), dict) else {}
        identity = native.get("line_id") or native.get("entry_stable_id") or ""
        # Native stable identity carries the precise threshold when the numeric
        # field has float32 noise (61.349998474121094 versus a stated 61.35).
        try:
            native_line = float(str(native.get("entry_stable_id", "")).rsplit("|", 1)[-1])
            if math.isfinite(native_line) and math.isclose(line, native_line, rel_tol=0, abs_tol=0.0001):
                line = native_line
        except (ValueError, TypeError):
            pass
        selections = raw.get("selection_by_side")
        if isinstance(selections, str):
            try:
                selections = json.loads(selections)
            except ValueError:
                selections = None
        for side in sides:
            # A multiplier belongs to one native selection, not the whole line.
            # Never borrow it from the opposite direction or infer it from odds.
            multiplier = None
            if book == "underdog" and raw.get("payout_multiplier_scope") == "entry" and isinstance(selections, dict):
                selection = selections.get({"Higher": "over", "Lower": "under"}.get(side))
                if isinstance(selection, dict):
                    value = selection.get("payout_multiplier")
                    try:
                        candidate = float(value)
                        if not isinstance(value, bool) and math.isfinite(candidate) and candidate > 0:
                            multiplier = candidate
                    except (TypeError, ValueError, OverflowError):
                        pass
            uid = hashlib.sha256(json.dumps([book, player, market, period, line, offer_type, side, event, identity], default=str).encode()).hexdigest()[:20]
            output.append({
                "Prop ID": uid, "Platform": BOOKS[book], "Player": player,
                "Market": f"Unverified: {source_market}" if market_status in INVALID_MARKET_STATUSES else MARKETS.get(market, (source_market or market, {}))[0],
                "Market key": market, "Side": side, "Line": line, "Period": period,
                "Source market": source_market, "Source market key": source_market_key,
                "Market status": market_status,
                "Offer": offer_type.replace("_", " ").title(), "Side basis": evidence,
                "Underdog multiplier": multiplier,
                "Matchup": " @ ".join(str(raw[k]) for k in ("away_team", "home_team") if raw.get(k)) or "Not supplied",
                "Game date": str(raw.get("game_date") or ""), "Tipoff": start, "Observed": observed,
                "Received": utc_time(raw.get("_received_at")), "Provider age": pd.to_numeric(raw.get("age_seconds"), errors="coerce"),
                "Injury": str(injury.get("status") or ""), "ESPN ID": str(raw.get("espn_id") or ""),
                "Event ID": event,
            })
    if not output:
        return pd.DataFrame()
    frame = pd.DataFrame(output)
    for column in ("Observed", "Tipoff", "Received"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame["Provider age"] = pd.to_numeric(frame["Provider age"], errors="coerce")
    frame["Underdog multiplier"] = pd.to_numeric(frame["Underdog multiplier"], errors="coerce")
    return frame.sort_values("Observed", na_position="first").drop_duplicates("Prop ID", keep="last").reset_index(drop=True)


def prepare_logs(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame = frame.rename(columns={"player": "PLAYER_NAME", "espn_id": "PLAYER_ID", "game_id": "GAME_ID", "game_date": "GAME_DATE", "season": "SEASON", "season_type": "SEASON_TYPE"})
    required = {"PLAYER_NAME", "GAME_ID", "GAME_DATE", "SEASON", "SEASON_TYPE", "MIN"}
    if not required <= set(frame):
        raise ValueError("Game logs are missing columns: " + ", ".join(sorted(required - set(frame))))
    for column in LOG_COLUMNS:
        if column not in frame:
            frame[column] = None
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"], utc=True, errors="coerce")
    for column in STAT_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["SEASON"] = frame.SEASON.astype(str)
    frame["SEASON_TYPE"] = frame.SEASON_TYPE.astype(str)
    frame["_name"] = frame.PLAYER_NAME.map(name_key)
    frame["PLAYER_ID"] = frame.PLAYER_ID.fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    valid = frame.GAME_DATE.notna() & frame.MIN.gt(0) & frame.GAME_ID.notna() & frame["_name"].ne("")
    # Zero statistical results with positive playing time are real appearances.
    return frame[valid].drop_duplicates(["_name", "GAME_ID", "SEASON", "SEASON_TYPE"]).sort_values("GAME_DATE", ascending=False).reset_index(drop=True)


def prop_history(prop, logs: pd.DataFrame, season: str, season_type: str, now=None) -> tuple[pd.DataFrame, str]:
    if prop.get("Market status") in INVALID_MARKET_STATUSES:
        return pd.DataFrame(), "Market metadata unverified; statistics withheld"
    spec = MARKETS.get(prop["Market key"])
    if " + " in prop["Player"]:
        return pd.DataFrame(), "Multi-player combo statistics unavailable"
    if prop["Period"] != "Full game":
        return pd.DataFrame(), "Period statistics unavailable"
    if spec is None:
        return pd.DataFrame(), "Market statistics unavailable"
    if logs.empty:
        return pd.DataFrame(), "History not loaded"
    if prop.get("ESPN ID"):
        matched = logs[logs.PLAYER_ID.eq(str(prop["ESPN ID"]))]
    else:
        matched = logs[logs["_name"].eq(name_key(prop["Player"]))]
    if matched.PLAYER_ID[matched.PLAYER_ID.ne("")].nunique() > 1:
        return pd.DataFrame(), "Ambiguous player; supply espn_id"
    matched = matched[matched.SEASON.eq(season) & matched.SEASON_TYPE.eq(season_type)]
    cutoff = utc_time(now or datetime.now(timezone.utc))
    if pd.notna(prop.get("Tipoff")):
        cutoff = min(cutoff, prop["Tipoff"])
    matched = matched[matched.GAME_DATE.lt(cutoff)].copy()
    if matched.empty:
        return pd.DataFrame(), "No matched games in season"
    weights = spec[1]
    matched["Value"] = sum(matched[column] * weight for column, weight in weights.items()).round(6)
    # A missing component must not silently shorten the hit-rate sample or streak.
    if matched.Value.isna().any():
        return pd.DataFrame(), "Incomplete statistics for market"
    if prop["Market key"] in {"player_double_double", "player_triple_double"}:
        count = matched[list(weights)].ge(10).sum(axis=1)
        matched["Value"] = count.ge(2 if prop["Market key"] == "player_double_double" else 3).astype(int)
    matched = matched.sort_values("GAME_DATE", ascending=False)
    if prop["Side"] == "Unspecified":
        matched["Result"] = "Unknown side"
    else:
        hits = matched.Value.gt(prop["Line"]) if prop["Side"] == "Higher" else matched.Value.lt(prop["Line"])
        matched["Result"] = hits.map({True: "Hit", False: "Miss"})
        matched.loc[matched.Value.eq(prop["Line"]), "Result"] = "Push"
    return matched, "Available"


def analyze(props: pd.DataFrame, logs: pd.DataFrame, season: str, season_type: str, now=None) -> pd.DataFrame:
    if props.empty:
        return props.copy()
    records = []
    # Many offers share the same player/market history. Cache that history within
    # this calculation instead of repeating a DataFrame scan for every line.
    histories = {}
    by_name = {key: group for key, group in logs.groupby("_name")} if not logs.empty else {}
    by_id = {key: group for key, group in logs.groupby("PLAYER_ID")} if not logs.empty else {}
    empty = logs.iloc[:0]
    for _, prop in props.iterrows():
        cache_key = (name_key(prop.Player), prop["ESPN ID"], prop["Market key"], prop.get("Market status"), prop.Period, str(prop.Tipoff))
        if cache_key not in histories:
            subset = by_id.get(str(prop["ESPN ID"]), empty) if prop["ESPN ID"] else by_name.get(name_key(prop.Player), empty)
            # The cached values are independent of this offer's side and line.
            neutral = {**prop.to_dict(), "Side": "Unspecified"}
            history, status = prop_history(neutral, subset, season, season_type, now)
            if status == "History not loaded" and not logs.empty:
                status = "No matched games in season"
            histories[cache_key] = (history.Value.to_numpy() if not history.empty else None, status)
        values, status = histories[cache_key]
        result = {column: None for column in METRIC_COLUMNS}
        if values is not None:
            last = values[:5]
            result.update({"L5 avg": last.mean(), "L5 games": len(last), "Season avg": values.mean(), "Games": len(values), "L5 vs line": last.mean() - prop.Line})
            if prop.Side != "Unspecified":
                hit_mask = values > prop.Line if prop.Side == "Higher" else values < prop.Line
                pushes = int((values == prop.Line).sum())
                hits = int(hit_mask.sum())
                misses = len(values) - hits - pushes
                streak = 0
                for hit in hit_mask:
                    if not hit:
                        break
                    streak += 1
                result.update({"Hits": hits, "Misses": misses, "Pushes": pushes, "Hit streak": streak, "Season hit %": 100 * hits / (hits + misses) if hits + misses else None})
            else:
                status = "Side unverified; averages only"
        records.append({**prop.to_dict(), **result, "History status": status})
    result = pd.DataFrame(records)
    for column in ("Observed", "Tipoff", "Received"):
        result[column] = pd.to_datetime(result[column], utc=True, errors="coerce")
    for column in METRIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def filter_board(frame: pd.DataFrame, *, platforms=(), markets=(), sides=(), offers=None, underdog_multiplier_range=None, periods=None, search="", min_streak=0, min_games=0, hit_range=(0, 100), l5_range=None, season_range=None, include_missing=True, upcoming_only=False, fresh_only=False, now=None) -> pd.DataFrame:
    """Apply shared research filters and independently scoped platform filters.

    offers applies only to PrizePicks. A multiplier range applies only to
    Underdog and excludes its unknown multipliers while the range is active.
    """
    if frame.empty:
        return frame.copy()
    mask = pd.Series(True, index=frame.index)
    for column, values in (("Platform", platforms), ("Market", markets), ("Side", sides)):
        mask &= frame[column].isin(values)  # An empty selection means no selections.
    if offers is not None:
        mask &= frame.Platform.ne("PrizePicks") | frame.Offer.isin(offers)
    if underdog_multiplier_range is not None:
        mask &= frame.Platform.ne("Underdog") | frame["Underdog multiplier"].between(*underdog_multiplier_range)
    if periods is not None:
        mask &= frame.Period.isin(periods)
    if search.strip():
        mask &= frame.Player.str.contains(search.strip(), case=False, regex=False) | frame.Matchup.str.contains(search.strip(), case=False, regex=False)
    if not include_missing:
        mask &= frame["Season hit %"].notna()
    for column, bounds, active in (
        ("Season hit %", hit_range, hit_range != (0, 100)),
        ("L5 avg", l5_range, l5_range is not None),
        ("Season avg", season_range, season_range is not None),
    ):
        if active:
            mask &= frame[column].between(*bounds)
    if min_streak:
        mask &= frame["Hit streak"].ge(min_streak)
    if min_games:
        mask &= frame.Games.ge(min_games)
    current = utc_time(now or datetime.now(timezone.utc))
    if upcoming_only:
        mask &= frame.Tipoff.gt(current)
    if fresh_only:
        observed_age = (current - frame.Observed).dt.total_seconds()
        # A reported age at receipt also bounds freshness; a new file isn't a new quote.
        provider_age = frame["Provider age"] + (current - frame.Received).dt.total_seconds().clip(lower=0).fillna(0)
        mask &= observed_age.between(0, 900) & (provider_age.isna() | provider_age.between(0, 900))
    return frame[mask].copy()
