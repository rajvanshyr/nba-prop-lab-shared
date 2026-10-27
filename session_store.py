"""Visitor-owned state. No credentials, paid boards or watchlists go to disk."""
from __future__ import annotations

from data import DataError, age_seconds, fetch_live_board, timestamp


def visitor_key(state, name="props_api_key") -> str:
    value = state.get(name, "")
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return "" if value.startswith("replace_with") else value


def clear_board_data(state):
    """Called when the visitor changes their props key, before the next render."""
    for name in ("session_board", "history", "history_key", "history_meta", "watchlist", "props_table", "research_messages", "research_error"):
        state.pop(name, None)


def clear_session(state):
    # Keep only the non-secret attempt time: clearing credentials must not cause
    # an accidental immediate second paid refresh in this same session.
    for name in list(state):
        if name != "last_props_attempt":
            del state[name]


def refresh_board(state, fetch=None) -> dict:
    """Explicit click only; missing credentials fail before any network call."""
    key = visitor_key(state)
    if not key:
        raise DataError("Enter your own ParlayAPI key in the sidebar before refreshing.")
    age = age_seconds(state.get("last_props_attempt"))
    if age < 300:
        raise DataError(f"Refresh is cooling down for {int(300 - age) + 1} seconds. Filtering and the session board use no credits.")
    state["last_props_attempt"] = timestamp()
    board = (fetch or fetch_live_board)(key)
    state["session_board"] = board
    return board
