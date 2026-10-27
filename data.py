"""Explicit-key ParlayAPI adapter and public-only ESPN disk cache.

Paid boards and credentials are managed by the caller in session memory.
This module never discovers keys or reads another app's data.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from metrics import LOG_COLUMNS, name_key, prepare_logs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "public_espn"
BOOK_KEYS = ("prizepicks", "underdog")
SEASON_TYPES = {"Regular Season": "Regular Season", "Playoffs": "Postseason", "Preseason": "Preseason"}


class DataError(RuntimeError):
    pass


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_season() -> str:
    now = datetime.now(timezone.utc)
    year = now.year if now.month >= 10 else now.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(payload, file, default=str, allow_nan=False)
        Path(temp).replace(path)
    finally:
        Path(temp).unlink(missing_ok=True)


def age_seconds(value) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return float("inf")


def fetch_live_board(key: str, get=requests.get) -> dict:
    """All market keys, separate book queries, <=6 calls / 18 estimated credits.

    There are no paid automatic retries. Exhausting pagination is distinct from
    proving upstream coverage; all completeness headers are retained.
    """
    if not isinstance(key, str) or not key.strip() or key.strip().startswith("replace_with"):
        raise DataError("Enter your own ParlayAPI key in the sidebar before refreshing.")
    key = key.strip()
    rows, requests_meta, errors = [], [], []
    request_count = 0
    partial = False
    stop = False
    for book in BOOK_KEYS:
        if stop:
            errors.append({"book": book, "status": "Not requested after rate limit/authentication failure"})
            partial = True
            continue
        offset = 0
        for page in range(3):
            request_count += 1
            try:
                response = get("https://parlay-api.com/v1/sports/basketball_nba/props", params={"bookmakers": book, "maxAgeSec": 900, "limit": 5000, "offset": offset}, headers={"X-API-Key": key}, timeout=(5, 30), allow_redirects=False)
            except requests.RequestException:
                errors.append({"book": book, "status": "Network error"})
                partial = True
                break
            headers = {name: response.headers.get(name) for name in ("x-result-has-more", "x-next-offset", "x-result-truncated", "x-result-truncated-hint", "x-result-degraded", "x-requests-remaining", "x-requests-last")}
            requests_meta.append({"book": book, "offset": offset, "status": response.status_code, "headers": headers})
            if response.status_code != 200:
                errors.append({"book": book, "status": f"HTTP {response.status_code}"})
                stop = response.status_code in (401, 403, 429)
                partial = True
                break
            try:
                body = response.json()
            except ValueError:
                body = None
            if not isinstance(body, list):
                partial = True
                errors.append({"book": book, "status": "Unexpected response shape"})
                break
            received = timestamp()
            rows.extend({**row, "_received_at": received} for row in body if isinstance(row, dict))
            if headers["x-result-truncated"] == "true" or headers["x-result-degraded"] or headers["x-result-has-more"] not in ("true", "false"):
                partial = True
            if headers["x-result-has-more"] != "true":
                break
            next_offset = str(headers["x-next-offset"] or "")
            if not next_offset.isdigit() or not offset < int(next_offset) <= 10000:
                partial = True
                break
            offset = int(next_offset)
        else:
            partial = True
    degraded_empty = not rows and any(r["headers"]["x-result-degraded"] or r["headers"]["x-result-truncated"] == "true" for r in requests_meta)
    if degraded_empty:
        raise DataError("The provider returned an empty, degraded or truncated board. The previous board was preserved.")
    if errors and not rows:
        raise DataError("Live refresh failed (" + "; ".join(f"{x['book']}: {x['status']}" for x in errors) + "). The previous board was preserved.")
    return {"rows": rows, "fetched_at": timestamp(), "source": "ParlayAPI · NBA · all returned markets", "metadata": {"partial": partial, "requests": requests_meta, "request_count": request_count, "estimated_credits": 3 * sum(r["status"] == 200 for r in requests_meta), "errors": errors, "scope": "All markets; separate PrizePicks and Underdog queries", "coverage": "Provider board only; upstream completeness cannot be guaranteed"}}


def public_json(url: str, params=None) -> dict:
    try:
        response = requests.get(url, params=params, headers={"User-Agent": "NBAPropExplorer/1.0"}, timeout=(5, 15))
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError
        return body
    except (requests.RequestException, ValueError):
        raise DataError("ESPN data is unavailable. Try again later or upload game logs.") from None


def player_directory(force=False) -> list[dict]:
    path = DATA / "espn_players.json"
    cached = read_json(path, {})
    if not force and cached.get("items") and age_seconds(cached.get("at")) < 86400:
        return cached["items"]
    items = []
    try:
        for page in range(1, 6):
            body = public_json("https://sports.core.api.espn.com/v3/sports/basketball/nba/athletes", {"limit": 1000, "page": page})
            if not isinstance(body.get("items"), list):
                raise DataError("Unexpected ESPN player directory.")
            items.extend({"id": str(row["id"]), "name": row.get("displayName") or row.get("fullName")} for row in body["items"] if row.get("id") and (row.get("displayName") or row.get("fullName")))
            if page >= body.get("pageCount", 1):
                break
        else:
            raise DataError("ESPN player directory exceeded its page limit.")
        save_json(path, {"at": timestamp(), "items": items})
        return items
    except DataError:
        if cached.get("items"):
            return cached["items"]
        raise


def parse_espn_log(body: dict, player: str, player_id: str, season: str) -> list[dict]:
    labels = body.get("labels", [])
    events = body.get("events", {})
    output = []
    for group in body.get("seasonTypes", []):
        title = group.get("displayName", "")
        if not title.startswith(season + " "):
            continue  # The provider can fall back to another year for a new player.
        kind = next((k for k, value in SEASON_TYPES.items() if title == f"{season} {value}"), None)
        if kind is None:
            continue
        for category in group.get("categories", []):
            if category.get("type") != "event":
                continue
            for game in category.get("events", []):
                event_id = str(game.get("eventId", ""))
                event = events.get(event_id, {})
                if event.get("gameResult") not in ("W", "L", "T"):
                    continue  # No ongoing, postponed, scheduled or DNP rows.
                stats = dict(zip(labels, game.get("stats", [])))
                row = {"PLAYER_NAME": player, "PLAYER_ID": player_id, "GAME_ID": event_id, "GAME_DATE": event.get("gameDate"), "SEASON": season, "SEASON_TYPE": kind, "TEAM": event.get("team", {}).get("abbreviation", ""), "MATCHUP": event.get("atVs", "") + " " + event.get("opponent", {}).get("abbreviation", "")}
                for label, column in {"MIN": "MIN", "PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "TO": "TOV", "PF": "PF"}.items():
                    value = str(stats.get(label, ""))
                    if column == "MIN" and ":" in value:
                        parts = value.split(":")
                        try:
                            value = float(parts[0]) + float(parts[1]) / 60
                        except (ValueError, IndexError):
                            value = None
                    row[column] = pd.to_numeric(value, errors="coerce")
                for label, made, attempted in (("FG", "FGM", "FGA"), ("3PT", "FG3M", "FG3A"), ("FT", "FTM", "FTA")):
                    parts = str(stats.get(label, "")).split("-")
                    row[made] = pd.to_numeric(parts[0], errors="coerce")
                    row[attempted] = pd.to_numeric(parts[1], errors="coerce") if len(parts) == 2 else float("nan")
                output.append({key: None if isinstance(value, float) and pd.isna(value) else value for key, value in row.items()})
    return output


def player_logs(player: str, player_id: str, season: str, force=False) -> tuple[list[dict], str]:
    if not player_id.isdigit():
        raise DataError("ESPN athlete IDs must be numeric.")
    path = DATA / "logs" / f"{season}-{player_id}.json"
    cached = read_json(path, {})
    if not force and "rows" in cached and age_seconds(cached.get("at")) < 3600:
        return [{**row, "PLAYER_NAME": player} for row in cached["rows"]], cached["at"]
    body = public_json(f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{player_id}/gamelog", {"season": int(season[:4]) + 1})
    if "seasonTypes" not in body or "labels" not in body:
        raise DataError("ESPN returned an incomplete game log.")
    rows = parse_espn_log(body, player, player_id, season)
    at = timestamp()
    # A caller may supply a private label through CSV/player-ID overrides.
    # Store only public stats and athlete IDs; apply its label in session memory.
    public_rows = [{key: value for key, value in row.items() if key != "PLAYER_NAME"} for row in rows]
    save_json(path, {"at": at, "rows": public_rows})
    return rows, at


def load_history(props: pd.DataFrame, season: str, overrides=None, force=False, progress=None) -> tuple[pd.DataFrame, dict]:
    if props.empty:
        return pd.DataFrame(), {"unmatched": [], "failed": [], "players": 0}
    directory = player_directory(force)
    lookup = {}
    for player in directory:
        lookup.setdefault(name_key(player["name"]), set()).add(player["id"])
    overrides = overrides or {}
    jobs, unmatched = [], []
    for player in sorted(props.Player.unique()):
        explicit = props.loc[props.Player.eq(player), "ESPN ID"]
        explicit = set(explicit[explicit.ne("")])
        supplied = str(overrides.get(player, ""))
        ids = {supplied} if supplied else explicit or lookup.get(name_key(player), set())
        if len(ids) != 1 or not next(iter(ids)).isdigit():
            unmatched.append(player)
        else:
            jobs.append((player, next(iter(ids))))
    rows, failed, fetched = [], [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(player_logs, player, player_id, season, force): player for player, player_id in jobs}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                part, at = future.result()
                rows.extend(part)
                fetched.append(at)
            except (DataError, OSError, ValueError, TypeError, KeyError):
                failed.append(futures[future])
            if progress:
                progress(done / max(1, len(jobs)))
    frame = prepare_logs(pd.DataFrame(rows, columns=LOG_COLUMNS))
    return frame, {"unmatched": unmatched, "failed": failed, "players": len(jobs), "oldest_fetch": min(fetched) if fetched else None, "source": "ESPN completed NBA game logs"}
