# YDA Member Portal — LunchTag

Member portal for the Yale Debate Association. The first feature is **LunchTag**, a weekly lunch-pairing system: it generates draft pairings, lets you edit and confirm them, and keeps history so people aren't repeatedly paired together.

The public YDA website (Squarespace) is separate and out of scope here — see [CLAUDE.md](CLAUDE.md) for the full architecture, governance rules, and roadmap.

## Deploy

Push to `main` on GitHub → Railway auto-builds and deploys. A failed build never deploys; the site stays on the last good version. Secrets and connection strings live in Railway environment variables (see `.env.example` for the variable names).

- Railway project: `yda-member-portal` (service `web` + Postgres plugin)
- Live URL: https://web-production-f7a671.up.railway.app
- Start command and restart policy live in `railway.json`; Python version is pinned in `.python-version`.

**Current caveats**: the deployed app still writes to ephemeral SQLite (wiped on every redeploy) — migrating storage to the Railway Postgres is the next step. There is no auth yet, so no real member data may be loaded into the deployed instance.

**Never commit the database, database dumps, member names/emails, or secrets.** The repo holds code and schema only.

## Run locally

1. Create a virtual environment and install deps:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Start the web UI:

```bash
python main.py
```

Open http://127.0.0.1:5000 in your browser.

### CLI (optional)

```bash
python -m lunchtag.cli players add "Ava"
python -m lunchtag.cli generate
python -m lunchtag.cli draft show
python -m lunchtag.cli draft confirm
python -m lunchtag.cli history list
```

## Auth

Sign-in is Google OAuth (OpenID Connect via Authlib), restricted to yale.edu accounts. It is **off by default**: if `GOOGLE_CLIENT_ID` is unset, the app runs with no login at all (current local-dev behavior). When it is set, every page requires sign-in except `/login`, `/auth/*`, and static files.

**Env vars** (see `.env.example`): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`. In production these live in Railway environment variables.

**Google Cloud Console setup** (one-time):

1. Create (or pick) a Google Cloud project → APIs & Services → OAuth consent screen. External user type is fine; add the app name and a contact email.
2. APIs & Services → Credentials → Create Credentials → **OAuth 2.0 Client ID**, application type **Web application**.
3. Add both **authorized redirect URIs**:
   - `http://localhost:5000/auth/callback`
   - `https://web-production-f7a671.up.railway.app/auth/callback`
4. Copy the client ID and secret into Railway env vars (or a local `.env`).

If the app ever moves to a custom domain, add the new `https://<domain>/auth/callback` redirect URI — that is step 2 of the domain-move checklist in CLAUDE.md.

**Sign-in policy** (data, not code — `lunchtag/auth/`):

- Always: the Google account email must be a *verified* `@yale.edu` address (checked against the ID token claims; the `hd` parameter is only a login hint and is never trusted).
- Optional whitelist: an `allowed_emails` table plus an enforcement toggle stored in the `app_settings` table (key `auth_whitelist_enforced`, default off). When on, only whitelisted yale.edu emails may sign in. No admin UI yet — manage it from a Python shell:

```python
from lunchtag.auth import store
store.add_allowed_email("someone@yale.edu")
store.list_allowed_emails()
store.set_whitelist_enforced(True)   # or False
```

**Roles** (`user_roles` table; default `member`): admins see the full dashboard (players, generate, history, reset); members see only their own profile page at `/me` — profile details on the left, a pairing-history widget on the right, and no top nav. First-time members get an onboarding screen at `/welcome` (display name, class year, residential college) which creates and links their player row; optional extras (birthday, favorite dining hall, fun facts) live in the `player_profiles` table and are editable via the collapsed "Edit profile" section.

**Strikes and boosts** (`player_preferences` table): members can strike players (never be paired with them — unlimited, private: no admin UI shows them) and boost one player (roughly doubles the odds of being paired — a new boost replaces the old). The engine treats strikes as hard constraints (`strike_penalty`) and boosts as a scoring bonus (`boost_bonus`, see `lunchtag/engine/config.py`). When strikes make perfect 2-person pairings impossible — even with an even roster — the generator falls back to groups of 3, so trios are now valid at any roster parity. With auth disabled (local dev), everyone is effectively admin. Manage roles with the CLI:

```bash
python -m lunchtag.cli role set someone@yale.edu admin
python -m lunchtag.cli role list
```

For a member to see their pairings, their email must be linked to their player: admins do this on the Players page (each card has a "Link yale.edu email" field), or via `python -m lunchtag.cli link someone@yale.edu <player_id>`. Run any CLI command against production by setting `DATABASE_URL` to the Railway Postgres connection string.

## Data & persistence

- SQLite file stored at `data/lunchtag.sqlite` (override with `LUNCHTAG_DATA_DIR`).
- Drafts are stored with status `draft`; only `confirmed` sessions count toward history.

## Scoring weights

Pairing score is the sum of penalties for every unordered pair inside each group:

- **Repeat penalty**: `count_weight * met_count^count_exponent`
- **Recency penalty**: if last met within `recency_window_days`, add `recency_weight * (1 - days/window)`
- **Fairness penalty**: discourage pairing two high-degree players via `fairness_weight * (deg_a + deg_b)/(2*max_degree)`
- **Trio penalty**: adds `trio_penalty` for any group of 3

Defaults live in `lunchtag/engine/config.py`.

## Tests

```bash
pytest
```

## Project layout

- `lunchtag/domain/` data models
- `lunchtag/storage/` SQLite repo
- `lunchtag/engine/` scoring + generator
- `lunchtag/auth/` Google sign-in, whitelist, settings
- `lunchtag/ui/` Flask UI
- `lunchtag/tests/` unit tests
