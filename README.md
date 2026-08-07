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
- `lunchtag/ui/` Flask UI
- `lunchtag/tests/` unit tests
