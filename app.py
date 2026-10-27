from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data import DataError, age_seconds, current_season, load_history
from demo import demo_data
from metrics import INVALID_MARKET_STATUSES, LOG_COLUMNS, analyze, filter_board, normalize_props, prepare_logs, prop_history, utc_time
from research_ui import render_chat
from session_store import clear_board_data, clear_session, refresh_board, visitor_key

st.set_page_config(page_title="NBA Prop Lab · Your keys", page_icon="🏀", layout="wide")
st.markdown("""<style>
    .block-container {padding-top:3.5rem; max-width:1800px}
    [data-testid="stSidebar"] {border-right:1px solid #253044}
    [data-testid="stMetric"] {background:#141d2b;border:1px solid #253044;border-radius:12px;padding:16px}
    [data-testid="stMetricLabel"] {color:#a2b2c8}
    h1 {letter-spacing:-1.7px;font-weight:750!important}
    .eyebrow {color:#b7f56c;letter-spacing:3px;font-size:12px;font-weight:700}
    .subtitle {color:#a2b2c8;font-size:16px;margin-bottom:24px}
</style>""", unsafe_allow_html=True)


def read_watchlist():
    return set(st.session_state.get("watchlist", []))


def calculate_board(props, logs, season, season_type):
    return analyze(props, logs, season, season_type)


def numeric_range(frame, column, label, key):
    if st.checkbox(f"Filter {label}", key=f"enable_{key}"):
        values = frame[column].dropna()
        low = float(values.min()) if not values.empty else 0.0
        high = float(values.max()) if not values.empty else 100.0
        a, b = st.columns(2)
        lower = a.number_input(f"Min {label}", value=low, step=1.0, key=f"{key}_low")
        upper = b.number_input(f"Max {label}", value=high, step=1.0, key=f"{key}_high")
        if lower > upper:
            st.caption("Minimum exceeds maximum; no rows will match.")
        return lower, upper
    return None


def table_frame(frame):
    display = frame.copy()
    display["Underdog multiplier"] = display["Underdog multiplier"].astype("Float64")
    display["Tipoff (PT)"] = display.Tipoff.dt.tz_convert("America/Los_Angeles").dt.strftime("%m/%d %I:%M %p").fillna("Unknown")
    display["Observed (PT)"] = display.Observed.dt.tz_convert("America/Los_Angeles").dt.strftime("%m/%d %I:%M %p").fillna("Unknown")
    columns = ["Player", "Platform", "Market", "Side", "Line", "Underdog multiplier", "L5 avg", "Season avg", "Hit streak", "Season hit %", "Hits", "Misses", "Pushes", "Games", "L5 games", "L5 vs line", "Offer", "Period", "Matchup", "Tipoff (PT)", "Injury", "History status", "Market status", "Source market", "Source market key", "Side basis", "Observed (PT)"]
    return display[columns]


def main():
    with st.sidebar:
        st.markdown('<div class="eyebrow">NBA PROP LAB</div>', unsafe_allow_html=True)
        st.caption("PrizePicks + Underdog")
        st.subheader("Your API keys")
        st.text_input("ParlayAPI key", type="password", key="props_api_key", on_change=clear_board_data, args=(st.session_state,), help="Use your own ParlayAPI key, not a PrizePicks or Underdog login. It stays in this session's server memory and is sent only to ParlayAPI for explicit refreshes. Changing it clears this session's board.")
        st.caption("Props requests use only your entered key and your ParlayAPI balance. No owner key is available.")
        st.markdown("[ParlayAPI account & documentation](https://parlay-api.com/docs)")
        st.button("Clear keys & session data", on_click=clear_session, args=(st.session_state,), width="stretch")
        st.caption("Keys, boards, watchlists and chat are temporary. A new browser session starts empty. Keys are sent to this app's server; use the hosted HTTPS address when deployed.")
        st.divider()
        source = st.selectbox("Board source", ["Live / session board", "Upload props CSV", "Demo (synthetic)"], key="board_source")
        latest = current_season()
        year = int(latest[:4])
        season = st.selectbox("Statistics season", [f"{y}-{str(y + 1)[-2:]}" for y in range(year, year - 5, -1)], key="statistics_season")
        season_type = st.selectbox("Season segment", ["Regular Season", "Playoffs", "Preseason"], key="season_segment")
        props_file = st.file_uploader("Props CSV", type="csv", key="props_upload") if source == "Upload props CSV" else None
        with st.expander("History & player matching"):
            logs_file = st.file_uploader("Optional game logs CSV", type="csv", key="logs_upload")
            st.caption("Otherwise, completed game logs load from ESPN and cache for one hour.")
            override_text = st.text_area("Player ID overrides (JSON)", value="{}", key="player_id_overrides", help='Exact provider name to numeric ESPN ID, e.g. {"Provider Name": "12345"}. Suffixes are preserved during automatic matching.')
            force_history = st.button("Reload ESPN history", width="stretch", disabled=source == "Demo (synthetic)")
        st.caption("Your ParlayAPI key is entered" if visitor_key(st.session_state) else "Enter your own ParlayAPI key to load live props")
        refresh = st.button("↻ Refresh live props", type="primary", width="stretch", disabled=source != "Live / session board" or not visitor_key(st.session_state))
        st.caption("Explicit refresh only. Up to 6 requests / 18 estimated credits; 5-minute cooldown. Filters use no credits.")

    board, demo_logs = None, None
    if source == "Demo (synthetic)":
        board, demo_logs = demo_data(season)
    elif source == "Upload props CSV":
        if props_file:
            try:
                raw = pd.read_csv(props_file).astype(object).where(lambda x: x.notna(), None)
                if not {"player", "bookmaker", "market_key", "line", "period"} <= set(raw):
                    raise ValueError("Props CSV requires player, bookmaker, market_key, line and period.")
                board = {"rows": raw.to_dict("records"), "fetched_at": None, "source": "Uploaded props CSV", "metadata": {"scope": "Uploaded rows only", "partial": True}}
            except (ValueError, pd.errors.ParserError) as error:
                st.error(str(error))
    else:
        if refresh:
            try:
                with st.spinner("Fetching all NBA markets from PrizePicks and Underdog…"):
                    board = refresh_board(st.session_state)
                st.success(f"Board loaded for this session · {board['metadata']['request_count']} requests · {board['metadata']['estimated_credits']} estimated credits from your account")
            except DataError as error:
                st.error(str(error))
        if board is None:
            board = st.session_state.get("session_board")

    st.markdown('<div class="eyebrow">PLAYER PROPS / RESEARCH BOARD</div>', unsafe_allow_html=True)
    st.title("Every line. A clearer view.")
    st.markdown('<div class="subtitle">NBA props across PrizePicks and Underdog, organized around recent form and season results.</div>', unsafe_allow_html=True)
    if source == "Demo (synthetic)":
        st.warning("DEMO MODE — All lines and game results below are synthetic. Switch board source for real data.")
    if not board:
        st.info("Enter your own ParlayAPI key in the sidebar, then click Refresh live props. You can also upload a props CSV or explore Demo mode without a key. Research chat optionally uses your own OpenAI key after a board is loaded.")
        templates()
        return
    metadata = board.get("metadata", {})
    fetched = board.get("fetched_at")
    fetched_time = utc_time(fetched)
    fetched_label = fetched_time.tz_convert("America/Los_Angeles").strftime("%b %d, %Y %I:%M %p PT") if pd.notna(fetched_time) else "not provided"
    st.caption(f"{board['source']}  ·  {season} {season_type}  ·  Board fetched: {fetched_label}")
    if fetched and age_seconds(fetched) > 900:
        st.warning("Saved board is older than 15 minutes. Lines may have moved or expired; refresh live props for a new observation.")
    if metadata.get("partial"):
        st.warning("Partial or unverified coverage. The table includes returned rows; it is not a complete inventory of both platforms.")
    props = normalize_props(board["rows"])
    if props.empty:
        st.info("No valid NBA PrizePicks or Underdog props were returned. This can happen between slates.")
        with st.expander("Feed status"):
            st.json(metadata)
        templates()
        return

    corrected_markets = int(props["Market status"].eq("Corrected source key using label").sum())
    unverified_markets = int(props["Market status"].isin(INVALID_MARKET_STATUSES).sum())
    if corrected_markets:
        st.info(f"Corrected provider market keys on {corrected_markets:,} prop sides using their source labels. Fantasy Points are analyzed as fantasy score, not points scored.")
    if unverified_markets:
        st.warning(f"Market metadata could not be verified on {unverified_markets:,} prop sides. Their historical statistics are withheld; inspect Market status and Source market in the table.")

    try:
        overrides = json.loads(override_text)
        if not isinstance(overrides, dict) or any(not str(v).isdigit() for v in overrides.values()):
            raise ValueError
    except ValueError:
        st.error("Player overrides must be a JSON object mapping names to numeric ESPN IDs.")
        overrides = {}
    for name, player_id in overrides.items():
        props.loc[props.Player.eq(name), "ESPN ID"] = str(player_id)

    history_meta = {}
    if demo_logs is not None:
        logs = demo_logs
        history_meta = {"source": "Synthetic demo history"}
    elif logs_file is not None:
        try:
            logs = prepare_logs(pd.read_csv(logs_file))
            history_meta = {"source": "Uploaded game logs", "rows": len(logs)}
        except (ValueError, pd.errors.ParserError) as error:
            st.error(str(error))
            logs = pd.DataFrame()
    else:
        # Public ESPN game logs may share a disk cache. Uploaded data and paid
        # board calculations remain in this visitor's session.
        history_key = (tuple(sorted(set(zip(props.Player, props["ESPN ID"])))), season, json.dumps(overrides, sort_keys=True), int(time.time() // 3600))
        if force_history or st.session_state.get("history_key") != history_key:
            try:
                bar = st.progress(0, text="Loading completed NBA game logs…")
                logs, history_meta = load_history(props, season, overrides, force_history, bar.progress)
                bar.empty()
                st.session_state.update(history_key=history_key, history=logs, history_meta=history_meta)
            except DataError as error:
                if "bar" in locals():
                    bar.empty()
                st.error(str(error))
                logs = pd.DataFrame()
        else:
            logs = st.session_state["history"]
            history_meta = st.session_state["history_meta"]
    with st.spinner("Calculating averages and hit rates…"):
        board_stats = calculate_board(props, logs, season, season_type)
    watchlist = read_watchlist()

    with st.sidebar:
        st.divider()
        st.subheader("Organize the board")
        search = st.text_input("Search player or matchup", placeholder="Player, team, matchup…", key="player_search")
        platforms = st.multiselect("Platforms", sorted(board_stats.Platform.unique()), default=sorted(board_stats.Platform.unique()), key="platforms")
        offers = None
        if "PrizePicks" in platforms:
            offer_options = sorted(board_stats.loc[board_stats.Platform.eq("PrizePicks"), "Offer"].unique(), key=lambda offer: ({"Standard": 0, "Goblin": 1, "Demon": 2}.get(offer, 3), offer))
            offers = st.multiselect("PrizePicks offer types", offer_options, default=offer_options, key="prizepicks_offers", help="Filters PrizePicks only. Underdog rows use their own multiplier filter.")
        multiplier_range = None
        if "Underdog" in platforms:
            if st.checkbox("Filter Underdog multiplier", key="filter_ud_multiplier", help="Filter the native entry payout multiplier for this row's Higher or Lower selection. Applies only to Underdog."):
                values = board_stats.loc[board_stats.Platform.eq("Underdog"), "Underdog multiplier"].dropna()
                maximum = float(values.max()) if not values.empty else 10.0
                a, b = st.columns(2)
                lower = a.number_input("Min Underdog multiplier (×)", min_value=0.0, value=0.0, step=0.05, format="%.2f", key="ud_multiplier_min")
                upper = b.number_input("Max Underdog multiplier (×)", min_value=0.0, value=maximum, step=0.05, format="%.2f", key="ud_multiplier_max")
                multiplier_range = (lower, upper)
                st.caption("Applies only to Underdog. Missing multipliers are excluded when enabled.")
                if lower > upper:
                    st.caption("Minimum exceeds maximum; no Underdog rows will match.")
        market_options = sorted(board_stats.Market.unique())
        selected_markets = st.multiselect("Market types", market_options, placeholder="All market types", key="market_types", help="Choose Points, Rebounds, Assists, combined stats, or other available markets. Leave empty to show all market types.")
        markets = selected_markets or market_options
        min_hit_rate = st.number_input("Minimum season hit rate (%)", min_value=0, max_value=100, value=0, step=1, key="min_season_hit_rate", help="For example, enter 70 to show props with a season hit rate of at least 70%. Pushes are excluded from the rate. A positive threshold also excludes unknown hit rates.")
        hit_range = (min_hit_rate, 100)
        sides = st.multiselect("Sides", sorted(board_stats.Side.unique()), default=sorted(board_stats.Side.unique()), key="sides")
        with st.expander("Periods"):
            periods = st.multiselect("Periods", sorted(board_stats.Period.unique()), default=sorted(board_stats.Period.unique()), key="periods")
        min_streak = st.number_input("Minimum consecutive hits", min_value=0, value=0, step=1, key="min_streak")
        min_games = st.number_input("Minimum season games", min_value=0, value=0, step=1, key="min_games")
        l5_range = numeric_range(board_stats, "L5 avg", "5-game average", "l5")
        season_range = numeric_range(board_stats, "Season avg", "season average", "season")
        include_missing = st.checkbox("Include missing history", value=True, key="include_missing")
        upcoming_only = st.checkbox("Upcoming tipoffs only", value=False, key="upcoming_only", help="Excludes unknown start times as well as started games.")
        fresh_only = st.checkbox("Observed within 15 minutes", value=False, key="fresh_only")
        watch_only = st.checkbox("Watchlist only", value=False, key="watch_only")
        sort_by = st.selectbox("Sort by", ["Season hit %", "Hit streak", "Underdog multiplier", "L5 avg", "Season avg", "L5 vs line", "Games", "Player", "Tipoff"], key="sort_by")
        ascending = st.checkbox("Ascending order", value=False, key="ascending")

    filtered = filter_board(board_stats, platforms=platforms, markets=markets, sides=sides, offers=offers, underdog_multiplier_range=multiplier_range, periods=periods, search=search, min_streak=min_streak, min_games=min_games, hit_range=hit_range, l5_range=l5_range, season_range=season_range, include_missing=include_missing, upcoming_only=upcoming_only, fresh_only=fresh_only)
    if watch_only:
        filtered = filtered[filtered["Prop ID"].isin(watchlist)]
    filtered = filtered.sort_values([sort_by, "Player"], ascending=[ascending, True], na_position="last", kind="stable").reset_index(drop=True)
    columns = st.columns(4)
    columns[0].metric("Prop sides shown", f"{len(filtered):,}", help="Each platform, line and side is a separate row.")
    columns[1].metric("Player entries", filtered.Player.nunique(), help="Unique player names or multi-player combo entries.")
    columns[2].metric("With season history", f"{filtered['Season avg'].notna().sum():,}")
    columns[3].metric("Markets", filtered.Market.nunique())
    st.write("")
    left, right = st.columns([4, 1])
    left.subheader("The prop board")
    right.download_button("↓ Export filtered CSV", table_frame(filtered).to_csv(index=False).encode(), file_name=f"nba_props_{season}.csv", mime="text/csv", width="stretch")
    st.caption(f"{len(filtered):,} of {len(board_stats):,} prop sides · Click a row for game history. Column headers also sort the table.")
    selected_prop = None
    if not filtered.empty:
        event = st.dataframe(
            table_frame(filtered), hide_index=True, width="stretch", height=540,
            on_select="rerun", selection_mode="single-row", key="props_table",
            column_config={
                "Player": st.column_config.TextColumn(width="medium"),
                "Market": st.column_config.TextColumn(width="medium"),
                "Line": st.column_config.NumberColumn(format="%.2f"),
                "Underdog multiplier": st.column_config.NumberColumn("UD multiplier (×)", format="%.2f", help="Source-reported entry payout multiplier for this specific side. This is not sportsbook odds or the total payout for a complete entry. Blank means not supplied or not applicable."),
                "L5 avg": st.column_config.NumberColumn("5-game avg", format="%.2f", help="Mean of up to five latest played games in the selected season and segment."),
                "Season avg": st.column_config.NumberColumn(format="%.2f"),
                "Hit streak": st.column_config.NumberColumn(format="%d", help="Consecutive latest played games beating this line on this side. A miss or push breaks the streak."),
                "Season hit %": st.column_config.ProgressColumn("Season hit %", format="%.1f%%", min_value=0, max_value=100, help="Hits / (hits + misses) against this line, excluding pushes."),
                "L5 vs line": st.column_config.NumberColumn(format="%+.2f"),
            },
        )
        selected = event.selection.rows
        selected_prop = filtered.iloc[selected[0] if selected and selected[0] < len(filtered) else 0]
    else:
        st.info("No props match these filters. Clear a filter or load another board.")

    render_chat(board_stats, logs, season, season_type, board, history_meta, selected_prop)
    if selected_prop is not None:
        details(selected_prop, logs, season, season_type, watchlist)

    with st.expander("Data coverage & metric definitions"):
        st.markdown("**5-game average:** mean of up to five latest played games in the selected season and segment. **Season average:** mean across that same season segment. **Hit streak:** consecutive most recent played games strictly beating this row's current line on its selected side; a miss or push ends it. **Hit rate:** hits ÷ (hits + misses); equal-to-line pushes are counted separately and excluded from the denominator. DNPs are excluded. All-push samples have no hit rate.")
        st.markdown("These are historical results against the **displayed line**, rather than the historical line each game had. They are descriptive statistics, not a win-probability model. Full-game history is used only for full-game offers; unsupported markets and periods remain visible without fabricated statistics. Last-five games never cross seasons. Platform-specific injury voids and entry payout rules are not simulated.")
        st.markdown("PrizePicks Standard Higher/Lower and Demon/Goblin Higher directions can be inferred from the declared projection type when native directions are missing. Those rows say **Projection type; verify in app**. Underdog directions come from the feed; missing directions remain unverified. Missing start times are preserved, not invented. Freshness uses observation time, not the time a saved file was opened.")
        st.markdown("**Underdog multiplier:** the source-reported entry payout multiplier attached to this row's Higher or Lower selection. Missing values stay blank; they are never assumed to be 1× or calculated from display odds. PrizePicks offer types filter PrizePicks only; the multiplier range filters Underdog only. Market, side, and historical-statistic filters apply to both platforms. An entry multiplier is distinct from the complete entry's final payout.")
        st.markdown("**Market verification:** source labels are checked against market keys. Known Underdog collisions—Fantasy Points keyed as points, and Blocks + Steals keyed as steals—are mapped to the labeled statistic before calculating averages, hits, or streaks. Other conflicts have no historical metrics. The source label/key and mapping status are included in the table and CSV. A saved quote does not establish that the line is still available in the platform's app.")
        st.write("Market verification", props["Market status"].value_counts().to_dict())
        st.write("History status", history_meta)
        if not logs.empty:
            matching = logs[logs.SEASON.eq(season) & logs.SEASON_TYPE.eq(season_type)]
            st.caption(f"Loaded {len(matching):,} played game rows for the selected season segment. Small early-season samples remain visible.")
        st.json(metadata)
        st.markdown("Integration reference: [ParlayAPI props documentation](https://parlay-api.com/docs#player-props). Historical source: [ESPN NBA](https://www.espn.com/nba/).")
        st.markdown("NBA fantasy score = PTS + 1.2×REB + 1.5×AST + 3×STL + 3×BLK − TOV, per [PrizePicks scoring](https://www.prizepicks.com/playbook-article/how-to-play-prizepicks-nba-fantasy-scoring-system) and [Underdog pick’em scoring](https://help.underdogsports.com/en/articles/10361785-pick-em-scoring-nba). Double/triple doubles are binary indicators from points, rebounds, assists, steals and blocks.")
    templates()


def details(prop, logs, season, season_type, watchlist):
    st.divider()
    a, b = st.columns([4, 1])
    a.subheader(f"{prop.Player} · {prop.Side} {prop.Line:g} {prop.Market}")
    a.caption(f"{prop.Platform} · {prop.Offer} · {prop.Period} · {season} {season_type}")
    if prop.Platform == "Underdog":
        if pd.notna(prop["Underdog multiplier"]):
            a.caption(f"Entry payout multiplier for this side: {prop['Underdog multiplier']:.2f}×")
        else:
            a.caption("Entry payout multiplier for this side: not supplied")
    saved = prop["Prop ID"] in watchlist
    if b.button("Remove from watchlist" if saved else "+ Add to watchlist", width="stretch"):
        if saved:
            watchlist.discard(prop["Prop ID"])
        else:
            watchlist.add(prop["Prop ID"])
        st.session_state["watchlist"] = sorted(watchlist)
        st.rerun()
    history, status = prop_history(prop, logs, season, season_type)
    if history.empty:
        st.info(status)
        return
    chart = history.head(20).sort_values("GAME_DATE")
    colors = chart.Result.map({"Hit": "#b7f56c", "Miss": "#63728c", "Push": "#e8bf67", "Unknown side": "#63728c"})
    fig = go.Figure(go.Bar(x=chart.GAME_DATE, y=chart.Value, marker_color=colors, customdata=chart[["MATCHUP", "Result"]].fillna("").values, hovertemplate="%{x|%b %d}<br>%{customdata[0]}<br>%{y:g} · %{customdata[1]}<extra></extra>"))
    fig.add_hline(y=prop.Line, line_dash="dash", line_color="#f2cc7a", annotation_text=f"Current line {prop.Line:g}")
    fig.update_layout(height=280, margin=dict(l=0, r=0, t=25, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#a2b2c8", xaxis_title=None, yaxis_title=prop.Market, bargap=0.28)
    st.plotly_chart(fig, width="stretch")
    st.caption("Latest 20 played games · lime = hit, slate = miss/unverified, gold = push · dashed line = displayed prop line")
    with st.expander("View game-by-game results"):
        display = history[["GAME_DATE", "TEAM", "MATCHUP", "MIN", "Value", "Result"]].rename(columns={"GAME_DATE": "Date", "MIN": "Minutes"})
        st.dataframe(display, hide_index=True, width="stretch")
        st.download_button("Download player history", display.to_csv(index=False).encode(), file_name="player_prop_history.csv", mime="text/csv")


def templates():
    with st.expander("CSV templates & standalone setup"):
        st.caption("Uploads are optional and stay in this session. Props use the ParlayAPI flat-row schema. Game logs must identify season and segment explicitly; blank statistics stay missing. Supply completed games only, one row per player and game.")
        a, b = st.columns(2)
        sample_row = {"bookmaker": "prizepicks", "player": "Example Player", "market_key": "player_points", "line": 20.5, "period": "FULL", "odds_type": "standard", "available_sides": '["over", "under"]', "commence_time": "", "game_date": "", "espn_id": "", "selection_by_side": "", "payout_multiplier_scope": ""}
        sample = pd.DataFrame([sample_row, {**sample_row, "bookmaker": "underdog", "selection_by_side": '{"over":{"payout_multiplier":1.18},"under":{"payout_multiplier":0.8}}', "payout_multiplier_scope": "entry"}])
        a.download_button("Props CSV template", sample.to_csv(index=False).encode(), "props_template.csv", mime="text/csv")
        b.download_button("Game logs CSV template", pd.DataFrame(columns=LOG_COLUMNS).to_csv(index=False).encode(), "game_logs_template.csv", mime="text/csv")
        st.code("cd nba_props_shared_app\npython3 -m venv .venv\nsource .venv/bin/activate\npip install -r requirements.txt\nstreamlit run app.py --server.port 8509", language="bash")


if __name__ == "__main__":
    main()
