"""Streamlit research chat, independent of the market table's controls."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from metrics import utc_time
from research import DEFAULT_MODEL, ResearchError, ask_research, build_context, lookup_props, source_link
from session_store import visitor_key


def context_label(selected) -> str:
    if selected is None:
        return "No row selected — name a player and market in your question."
    tipoff = utc_time(selected.get("Tipoff"))
    time_label = tipoff.tz_convert("America/Los_Angeles").strftime("%b %d, %Y %I:%M %p PT") if pd.notna(tipoff) else "unknown"
    return f"{selected['Player']} · {selected['Platform']} · {selected['Side']} {selected['Line']:g} {selected['Market']} · {selected['Period']} · tipoff {time_label}"


def render_chat(board, logs, season, segment, snapshot, history_meta, selected=None):
    st.divider()
    st.subheader("Ask the dashboard")
    st.caption("Research a line, check injury and rotation news, or ask about another player. Answers search the web and link to sources.")
    label = context_label(selected)
    st.info("Current context: " + label)
    with st.expander("OpenAI chat setup", expanded=not bool(visitor_key(st.session_state, "research_api_key"))):
        st.text_input("OpenAI API key", type="password", key="research_api_key", help="Use your own OpenAI API key. It stays in this session's server memory, is sent only to OpenAI for research requests, and isn't saved to disk or included in prompts. There is no server-key fallback.")
        st.caption("Research uses your own OpenAI balance. Leaving this blank disables chat; the props table still works.")
        model = st.text_input("Research model", value=DEFAULT_MODEL, key="research_model", help="Use an OpenAI Responses model with web-search support. Default: gpt-5.4-mini.")
        max_searches = st.number_input("Maximum web tool calls per question", min_value=1, max_value=5, value=3, step=1, key="research_search_limit")
        st.caption("Your question, recent chat, selected prop and relevant board lookups are sent to OpenAI. Calls run only when you submit a question. Model and web-search usage are billed by OpenAI; this does not use ParlayAPI credits.")
    key = visitor_key(st.session_state, "research_api_key")
    if not key:
        st.info("Add an OpenAI API key in chat setup to enable questions. Keep the key out of chat messages.")
    if str(snapshot.get("source", "")).startswith("DEMO"):
        st.caption("Demo lines are synthetic. Research can investigate real news, but it cannot validate a synthetic pick.")
    messages = st.session_state.setdefault("research_messages", [])
    if st.button("Clear research chat", key="clear_research_chat", disabled=not messages):
        st.session_state["research_messages"] = []
        st.session_state.pop("research_error", None)
        st.rerun()
    with st.container(border=True):
        for message in messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message.get("context_label"):
                    st.caption(message["context_label"])
                if message.get("sources"):
                    with st.expander("Research sources"):
                        for source in message["sources"]:
                            st.markdown("- " + source_link(source["title"], source["url"]))
                if message.get("searched_at"):
                    when = pd.Timestamp(message["searched_at"]).tz_convert("America/Los_Angeles").strftime("%b %d, %Y %I:%M %p PT")
                    usage = message.get("usage", {})
                    st.caption(f"Researched {when} · {message['model']} · {message['search_calls']} web tool calls · {usage.get('input_tokens', 0):,} input / {usage.get('output_tokens', 0):,} output tokens")
        if st.session_state.get("research_error"):
            st.error(st.session_state["research_error"])
        a, b = st.columns(2)
        explain = a.button("Why is this line high?", key="research_explain", disabled=not key or selected is None, width="stretch")
        news = b.button("Check injuries & rotation", key="research_news", disabled=not key or selected is None, width="stretch")
        question = st.chat_input("Why is Yaxel's points line high? What news changes the risks?", key="research_question", disabled=not bool(key), max_chars=3000)
        if explain:
            question = "Why might this selected line be so high relative to recent form? Verify the player, game, injuries and expected role, and explain the risks on both Higher and Lower with current sources."
        elif news:
            question = "Check current injury reports and rotation news for this selected player's exact game. What is confirmed, how might it affect minutes and usage, and what remains uncertain? Cite dated sources."
        if question:
            context = build_context(selected, board, logs, season, segment, snapshot.get("source", "Unknown"), snapshot.get("fetched_at"), history_meta)
            prior_messages = list(messages)
            user_message = {"role": "user", "content": question, "context_label": label}
            try:
                with st.spinner("Searching current news and checking the dashboard…"):
                    result = ask_research(question, key=key, model=model.strip(), context=context, history=prior_messages, max_searches=max_searches, lookup=lambda player, market: lookup_props(board, logs, season, segment, player, market))
            except ResearchError as error:
                # No partial analysis is displayed on search/citation failure.
                st.session_state["research_messages"] = (messages + [user_message])[-20:]
                st.session_state["research_error"] = str(error)
            else:
                st.session_state["research_messages"] = (messages + [user_message, {"role": "assistant", **result}])[-20:]
                st.session_state.pop("research_error", None)
            st.rerun()
    if messages:
        export = [{key: value for key, value in message.items() if key != "usage"} for message in messages]
        st.download_button("Download research conversation", json.dumps(export, indent=2).encode(), file_name="nba_prop_research.json", mime="application/json", key="download_research_chat")
