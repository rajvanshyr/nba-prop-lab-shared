# NBA Prop Lab — visitor API keys

A separate, deployment-ready Streamlit version of NBA Prop Lab. This folder is
self-contained: it does not import, modify, or read data from `nba_props_app` or
`underdog-lab-starter`.

## Use the app

1. Enter **your ParlayAPI key** in the sidebar, then click **Refresh live props**.
   This is a ParlayAPI developer key, not a PrizePicks or Underdog login.
   See [ParlayAPI](https://parlay-api.com/docs) for an account and API access.
2. Filter PrizePicks/Underdog player props by market, side, PrizePicks offer type,
   Underdog side multiplier, 5-game average, season average, streak, and season
   hit rate. Clicking a row shows its game history and supplies chat context.
3. Optionally enter **your OpenAI API key** under **OpenAI chat setup** below the
   table to research injuries, rotations, and line context with cited web search.
   Without that key, the table still works and chat is disabled.
4. **Clear keys & session data** removes keys, the live board, uploads, watchlist,
   filters and conversation from the current app session. Export filtered props
   or research before clearing if you want to keep them.

Demo mode and CSV uploads work without API keys. Demo data is explicitly
synthetic. Uploads are optional and remain in the current session.

## Credentials, billing, and isolation

- Each paid request requires the key explicitly entered in that visitor's
  session. Empty, placeholder or rejected keys never fall back to any other key.
- The app never reads credentials from environment variables, `.env`, Streamlit
  secrets, the original app, or the starter project. Do **not** add your personal
  API keys to this deployment's environment or secret settings.
- Keys are masked inputs and are held in server memory for the active Streamlit
  session. They are not written to disk, global caches, exports, URL parameters,
  prompts, or application logs. This is session isolation, not a separate server
  or encryption boundary: the hosting operator runs the backend that handles
  the entered credentials. Publish using HTTPS.
- Live boards, uploaded data, computed boards, watchlists, and chats are held in
  session memory, with no global Streamlit data cache. Changing the ParlayAPI key
  clears the loaded board, derived history, watchlist and chat before rendering.
  Different visitors and browser sessions start with empty state. A browser
  reload/reconnection or host restart may reset session data; there are no
  accounts, persistent watchlists, or saved credentials.
- Only **public ESPN** player lists and completed game logs are cached to disk,
  under this copy's `data/public_espn/`. No paid board is saved there.
- Props refresh is explicit, capped at three pages per book / six calls total
  (up to 18 estimated credits), with no paid automatic retries and a five-minute
  cooldown in that session. Failed refreshes preserve its prior board. This
  cooldown is an accidental-repeat guard, not an account-wide spending cap;
  starting another session resets it.
- Chat requests are explicit and billed to the visitor's OpenAI key. They use
  the existing Responses/web-search integration, `store: false`, up to three
  API rounds, a bounded number of board lookups, and 1–5 web tool calls per
  question (default 3). Errors are sanitized and incomplete/uncited answers are
  withheld. [Official OpenAI authentication documentation](https://developers.openai.com/api/reference/overview#authentication)

Never paste an API key into player search, CSVs, or chat text. These controls
prevent the app from silently using an owner's key; they do not set spending
limits for a visitor's own provider account.

## Run independently

```bash
cd nba_props_shared_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py --server.port 8509
```

On this Mac, double-click `Launch Shared NBA Props.command` to use this folder's
virtual environment or the existing parent environment. The launcher opens a
localhost-only server on port **8509**, leaving the original app on **8508**.

Run tests without paid requests:

```bash
python -m unittest discover -s tests -v
```

## Deploy later on Streamlit Community Cloud

Deployment has not been performed. When ready:

1. Create a **new** GitHub repository containing the contents of this folder at
   its root (`app.py`, `requirements.txt`, `.streamlit/config.toml`, etc.).
   Exclude `.env*`, `.streamlit/secrets.toml`, `.venv/`, and `data/` using the
   supplied `.gitignore`. Do not upload the parent project or original app.
2. In Community Cloud, create an app from that repository and choose `app.py`
   as the entrypoint. Python 3.12 is suitable for the declared dependencies.
3. Leave the hosting API-key/secrets settings empty. Visitors provide their own
   keys through the app; no keys need to be configured on the host.
4. Deploy and share the provided HTTPS URL. Updates to this repository affect
   this copy only.

See [Streamlit's deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).

## Data interpretation

The app preserves the original market/side validation, native Underdog
multipliers, and source-label corrections for miskeyed fantasy points and
blocks + steals. It never invents missing Underdog Lower offers. Upstream
coverage may be partial; a saved quote does not prove availability in either
platform's app.

ESPN histories use completed games in the selected season and segment. Last-five
averages never cross seasons. Hits are evaluated against the displayed current
line; pushes are excluded from hit-rate denominators and break streaks. Missing
or unsupported statistics remain blank. These are descriptive results, not a
calibrated probability or automatic betting recommendation.
