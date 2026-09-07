# YDA Member Portal

Member resources portal for the Yale Debate Association. Features live as tabs; the first (and currently only) live feature is **LunchTag**, a weekly lunch-pairing system: it generates draft pairings honoring member strikes/boosts, lets admins edit and confirm them, and keeps history so people aren't repeatedly paired together. Future features (YDBoba, Events Schedule, Tournament Registration, Reimbursements) are registered in `TABS` but hidden until they ship.

The public YDA website (Squarespace) is separate and out of scope here — see [CLAUDE.md](CLAUDE.md) for the full architecture, governance rules, and roadmap.

**Never commit the database, database dumps, member names/emails, or secrets.** The repo holds code and schema only.

## Deploy

Push to `main` on GitHub → Railway auto-builds and deploys. A failed build never deploys; the site stays on the last good version. Secrets and connection strings live in Railway environment variables (see `.env.example` for the variable names).

- Railway project: `yda-member-portal` (service `web` + Postgres plugin)
- Live URL: https://lunchtag.yaledebate.org
- Start command (`gunicorn portal.app:app`) and restart policy live in `railway.json`; Python version is pinned in `.python-version`.
- In production the app uses Postgres (`DATABASE_URL` set); locally and in CI it uses SQLite. Same schema, same code.

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

Open http://127.0.0.1:5000 in your browser. With no `GOOGLE_CLIENT_ID` set, auth is disabled and you get full admin access locally.

### CLI (optional)

```bash
python -m portal.cli players add "Ava"
python -m portal.cli generate
python -m portal.cli draft show
python -m portal.cli draft confirm
python -m portal.cli history list
python -m portal.cli role set someone@yale.edu admin
python -m portal.cli link someone@yale.edu 12
```

Run any CLI command against production by setting `DATABASE_URL` to the Railway Postgres connection string.

## Project layout — how features work

```
portal/
  app.py                  # app factory + TABS registry (add new features here)
  auth/                   # Google sign-in, roles, whitelist, email→player links
  storage/                # shared schema (db.py) + repository (repo.py)
  domain/models.py        # shared dataclasses
  templates/, static/     # app shell: base.html (tab bar), login, onboarding
  features/
    profile/              # blueprint: /profile, /welcome — identity only
    lunchtag/             # blueprint: /lunchtag — pairings, strikes/boosts, engine/, admin pages
  tests/
```

**To add a feature X**: copy the shape of `portal/features/lunchtag/` — a `routes.py` with one blueprint (`template_folder="templates"`, templates under `templates/x/`), register it in `portal/app.py`, flip its `TABS` entry to `enabled: True` (disabled entries are hidden), add `portal/tests/test_x.py`. Admin-only routes must have endpoint names starting with `admin_` — the auth gate blocks members from any such endpoint. New tables: `CREATE TABLE IF NOT EXISTS` in a feature-local store module (see `portal/auth/store.py`) or in `portal/storage/db.py` if shared.

## Auth

Sign-in is Google OAuth (OpenID Connect via Authlib), restricted to yale.edu accounts. It is **off by default**: if `GOOGLE_CLIENT_ID` is unset, the app runs with no login at all (local dev). When set, every page requires sign-in except `/login`, `/auth/*`, and static files.

**Env vars** (see `.env.example`): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`; optional `DUMMY_LOGIN_EMAIL` (testing backdoor — never set once real member data is loaded).

**Google Cloud Console** (one-time, already done): OAuth 2.0 Client ID (Web application) with redirect URIs `http://localhost:5000/auth/callback`, `https://lunchtag.yaledebate.org/auth/callback`, and the fallback `https://web-production-f7a671.up.railway.app/auth/callback`. If the app moves to a custom domain, add `https://<domain>/auth/callback` — step 2 of the domain-move checklist in CLAUDE.md.

**Sign-in policy** (data, not code — `portal/auth/`): the email must be a *verified* `@yale.edu` address (from ID-token claims; `hd` is only a hint). Optional whitelist: `allowed_emails` table + enforcement toggle in `app_settings` (key `auth_whitelist_enforced`, default off), managed via `portal.auth.store` functions.

**Roles** (`user_roles` table; default `member`): members see the Profile and LunchTag tabs only; admins additionally get the admin sub-nav on LunchTag pages (Players, Generate, History, Reset). First-time members onboard at `/welcome` (welcome splash, then a setup form that creates + links their player row); profile extras live in `player_profiles`.

**LunchTag opt-in**: participation is a member choice (`lunchtag_opt_in` on `player_profiles`). New users are opted **out** by default — onboarding and the Profile page have a prominent toggle to join. Opted-out members see a friendly "opt in from your profile" page instead of the LunchTag dashboard, and pairing generation draws only from `list_pairing_pool()` (active + explicitly opted in). Onboarding-tester dummy accounts (emails starting `onboard`) reset themselves on every dummy login so the first-run flow can be replayed endlessly.

**Strikes and boosts** (`player_preferences` table): members can strike players (never paired — unlimited, private: no admin UI shows them) and boost one player (~doubles pairing odds; new boost replaces old). Strikes are hard engine constraints; when they make perfect pairs impossible — even with an even roster — the generator falls back to groups of 3.

## Notifications (email + Slack)

When an admin confirms a draft, every member with a linked email gets a pairing email (partner names + their profile info), and a summary posts to Slack. A Railway cron service (`railway.cron.json`: Sundays 23:00 UTC) runs `python -m portal.cli remind-admins`, emailing every admin a reminder to push new pairings.

Members control their own delivery: the LunchTag page has Email/Slack notification toggles (both on by default, stored on `player_profiles`); opted-out members are skipped and counted in the admin's confirmation summary. The email follows the board's template (partner name, class year, college, major(s) unless Undeclared, fun facts, dining hall — missing facts drop their sentence) and is signed by the Social Chair.

Admins audit and edit **every** automated message on the **Message templates** admin page — pairing email (subject + body), pairing Slack DM, the weekly #lunchtag announcement, and the Sunday admin reminder (subject + body). Each is sandboxed Jinja stored in `app_settings` (registry: `TEMPLATES` in `portal/notifications.py`), with a live sample-data preview and one-click reset. A broken saved template can never block sends — the built-in default is the automatic fallback, and the page surfaces the error.

Two independent safety layers, both off by default:

1. **Master switch** (config-as-data): `python -m portal.cli notifications on|off|status`.
2. **Credentials**: with no email/Slack env vars, everything is a dry-run — counted and logged, nothing sent. Email transports, in preference order: **Gmail API** (sends from a personal account; one-time `gmail.send` consent via `python scripts/get_gmail_token.py` — see that file for the full recipe) or any **SMTP** provider. `PORTAL_BASE_URL` controls links in messages.

**Slack** (`portal/slack.py`, bot token from the LunchTag app at api.slack.com — scopes listed in `.env.example`): on confirm, each linked member with the Slack toggle on gets a DM with their pairing (looked up by their linked email via `users.lookupByEmail`; members whose Slack email differs are counted as "not found in Slack"), and the bot posts a "pairings are out" notice to `SLACK_CHANNEL`. The admin **Slack sync** page matches roster players to workspace members by name: exact matches with a verified `@yale.edu` Slack email are auto-linkable in one click; Gmail-registered and unmatched members are listed for manual linking on the Players page. Non-roster Slack members are never imported — new members onboard themselves on the site. The old `SLACK_WEBHOOK_URL` remains as a fallback for the channel post only.

The cron service must have "Config file path" set to `railway.cron.json` in its Railway settings (otherwise it would inherit the web start command from `railway.json`), plus the same `DATABASE_URL` reference and `SMTP_*` vars as the web service.

## Data & persistence

- Local: SQLite at `data/lunchtag.sqlite` (override dir with `LUNCHTAG_DATA_DIR`). Production: Postgres via `DATABASE_URL`.
- Drafts are stored with status `draft`; only `confirmed` sessions count toward history.

## Scoring weights

Pairing score is the sum of penalties for every unordered pair inside each group:

- **Repeat penalty**: `count_weight * met_count^count_exponent`
- **Recency penalty**: if last met within `recency_window_days`, add `recency_weight * (1 - days/window)`
- **Fairness penalty**: discourage pairing two high-degree players via `fairness_weight * (deg_a + deg_b)/(2*max_degree)`
- **Trio penalty**: adds `trio_penalty` for any group of 3
- **Strike/boost/singleton**: `strike_penalty` (hard constraint), `boost_bonus` (attraction), `singleton_penalty` (last-resort lone group)

Defaults live in `portal/features/lunchtag/engine/config.py`.

## Tests

```bash
pytest
```
