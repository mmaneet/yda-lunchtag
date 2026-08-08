# CLAUDE.md — YDA Member Portal

## What this project is

This repo contains the member portal for the Yale Debate Association (YDA), intended to live at a subdomain of yaledebate.org (eventually `members.yaledebate.org`; possibly `lunchtag.yaledebate.org` first). The public YDA website is a separate Squarespace site and is **out of scope for this repo** — the Squarespace site handles public content (recruiting, donations, history) and simply links to this portal. Do not propose migrating the public site into this codebase; the split is deliberate and permanent.

The first and currently only feature is **LunchTag**: a weekly lunch-pairing system for YDA members. There is an existing local prototype — a pairing engine plus a local SQL database containing historic pairing data. The engine and its history predate this repo.

## Who maintains this

The maintainer profile is the single most important architectural constraint:

- Right now: Maneet (technical, Yale '29, graduates and leaves).
- Later: a rotating, possibly non-technical YDA "web officer" whose entire maintenance workflow is prompting a Claude Code session against this repo.

**Every decision should optimize for a future Claude Code session with zero context being able to make a correct change end-to-end.** Concretely:

- One monorepo, one deployed service, one framework. Never propose splitting into multiple services or repos.
- Boring, mainstream stack only (see Stack below). No niche frameworks, no clever abstractions.
- Strong file-level convention: every feature should look like every other feature, so "add X like Y" is a mechanical transformation. Concretely (since Aug 2026): a feature is a Flask blueprint package at `portal/features/<name>/` (routes.py + `templates/<name>/`), registered in `portal/app.py` with a `TABS` entry (disabled entries render as faded "Coming Soon!" tabs). Admin-only routes must have endpoint names starting with `admin_` — the auth gate blocks members from those by convention. Feature-local tables use `CREATE TABLE IF NOT EXISTS` store modules (pattern: `portal/auth/store.py`); shared tables live in `portal/storage/db.py`. See README "Project layout" for the copyable recipe.
- Config as data: semester dates, pairing frequency, feature toggles, budget caps belong in a database table (editable via an eventual admin panel), not in code constants.
- This file must be kept current. When architecture or workflows change, update CLAUDE.md in the same commit.

## Stack and architecture

- **App**: Single full-stack app (Next.js + TypeScript preferred; if the existing engine is Python, discuss tradeoffs with Maneet before choosing — porting the engine to TS vs. keeping a Python service is an open decision, but the one-service rule wins either way).
- **Database**: Postgres, hosted as a Railway plugin alongside the app. The local historic pairing data must be migrated into it (via pg_dump/restore if local DB is Postgres; via schema translation if SQLite/MySQL).
- **ORM/migrations**: Prisma or Drizzle. All schema changes go through committed migration files, applied on deploy. Never edit production schema by hand.
- **Auth (planned, not yet built)**: Google OAuth restricted to yale.edu (Yale College students have EliApps Google accounts). One `role` column: member / board / admin. Keep auth dead simple; never store passwords.
- **Hosting**: Railway (Hobby plan, ~$5/mo). Persistent container model chosen over serverless because the existing engine runs as a normal process and the weekly pairing job is a natural cron task. Set a spending cap in Railway settings.
- **Deploy loop**: push to `main` on GitHub → Railway auto-builds and deploys. A failed build never deploys; the site stays on the last good version. This loop is the whole maintenance story — protect it.
- **Scheduled jobs**: Railway cron triggers the weekly pairing run (Monday mornings during term).

## Guardrails (do not weaken these)

1. TypeScript + build step: most LLM mistakes should fail the build, not reach users.
2. A small set of smoke tests in GitHub Actions on every push (e.g., "pairing engine produces valid pairings with no repeats inside the lookback window," "app boots"). Tripwires, not comprehensive coverage.
3. Preferred change workflow for non-trivial edits: open a PR, review the preview/staging behavior, then merge. Trivial copy edits may go straight to main.
4. **Never commit the database, database dumps, or any file containing member names/emails to the repo.** The repo holds code and schema only. Member data lives exclusively in the database.
5. **Never commit secrets.** Connection strings, OAuth credentials, and API keys live in Railway environment variables. `.env` is gitignored; maintain a `.env.example` with variable names only.
6. Do not touch auth logic or any data-deletion path without explicitly flagging it to the human and getting confirmation.
7. Nightly database backups via Railway's built-in backups must remain enabled.

## Governance rules

- The GitHub repo must live in a **YDA GitHub organization**, not a personal account. Maneet is a member/admin of the org, not the owner of the repo namespace.
- The Railway project should ultimately be owned by an official YDA Railway account (role email, org card, 2FA), with individual maintainers added as project members. If prototyping starts on a personal account, transferring requires both accounts subscribed — plan the transfer as an explicit task, not an afterthought.
- The DNS login for yaledebate.org is the org's most powerful credential. Domain changes require: (1) DNS record update, (2) OAuth redirect URI update in Google Cloud console, (3) `BASE_URL`-type env var update. That three-item checklist is the complete domain-move procedure.

## Product roadmap (context for future features)

Sequenced by behavior-change required, not build cost. Features that require board members to change their workflow are built **only when a board member asks** ("pull, not push").

1. **Tier 1 — live or next (nobody changes behavior)**: LunchTag; YDBoba (same engine, different config — build as one parameterized pairing system, not two features); read-only tournament calendar; a links hub collecting existing scattered signup sheets and team docs. Strikes (unlimited, private — no admin UI shows them) and boosts (one at a time, ~doubles pairing odds) shipped Aug 2026 as `player_preferences` + engine constraints; trio fallback covers strike graphs with no perfect matching.
2. **Tier 2 — members change, board doesn't**: practice RSVPs, judge signups, tournament interest forms. Output remains "a Google Sheet the board reads" — the portal writes one-directionally to existing sheets via the Sheets API so board workflow is untouched.
3. **Tier 3 — by invitation only**: treasurer reimbursement view (status loop: submitted → approved → paid, with member-facing status page), admin dashboard (manual pairing trigger, roster edit, feature toggles, usage counters), roster/directory, institutional-memory wiki.

Instrument from day one: simple counters (pairings completed, opt-out rate, signups processed) surfaced in an admin view. These numbers are the fall pitch to the YDA board.

## Current state and the proximate goal

**State** (updated 2026-08-07): The app is now structured as a multi-feature portal (`portal/` package, feature blueprints under `portal/features/`, tabbed UI with Profile + LunchTag live and four Coming-Soon tabs: YDBoba, Events Schedule, Tournament Registration, Reimbursements). Code lives at github.com/mmaneet/yda-member-portal (private, personal account — YDA org transfer pending, see Governance). Deployed on Railway (project `yda-member-portal`, service `web` + Postgres) at https://web-production-f7a671.up.railway.app, with GitHub→Railway auto-deploy on push to `main` confirmed working. The app is dual-backend: Postgres when `DATABASE_URL` is set (production), SQLite otherwise (local dev, CI). Historic pairing data (46 players, 6 sessions) has been migrated into Railway Postgres and verified live. UI is Yale-styled (white/gray/Yale Blue, YDA logo). Google OAuth (yale.edu-restricted, Internal consent screen) is **live in production**. Roles exist per the auth plan: `user_roles` table, default `member`; members see only `/me` (profile screen with pairing-history widget, no top nav), admins see the full dashboard; maneet.mehta@yale.edu is admin. First-time members onboard at `/welcome` (splash → setup form: display name, class year, college, and a LunchTag opt-in toggle — opted OUT by default for new users; existing members were backfilled to opted-in). Pairing generation uses only opted-in players (`list_pairing_pool`). Optional profile extras live in `player_profiles`. Dummy-login emails starting with `onboard` auto-reset on login for replaying onboarding. A dummy-login backdoor exists while the `DUMMY_LOGIN_EMAIL` Railway env var is set — unset it to remove, and never set it once real member data is loaded. The whitelist scaffold remains dormant (toggle off). Notifications (pairing emails on confirm + Slack post + Sunday-evening admin reminder via a Railway cron service running `python -m portal.cli remind-admins`) are built but dormant: dry-run until SMTP/Slack env vars exist AND the `notifications_enabled` setting is flipped on via CLI. Still pending: SMTP + Slack credentials, Railway spending cap, backup verification, YDA org transfers, Google Cloud project co-ownership. Maneet plans to reset the pairing data before the semester starts; the OAuth client secret was pasted in a chat once and should be rotated at the same time.

**The immediate task, in order. Work through this checklist with Maneet; don't skip ahead:**

1. **Repo migration**: Bring the local project into this Git repo with sane structure. Separate the pairing engine as its own module from any web layer. Add `.gitignore` (env files, DB files, build artifacts), `.env.example`, and a README covering: what this is, how to run locally, how deploy works.
2. **Audit before first push**: Verify no member data, no DB dumps, no secrets anywhere in the tree or history. If the local project has them embedded, scrub before the initial commit — history is forever.
3. **GitHub org setup**: Create the YDA GitHub organization (free) if it doesn't exist; repo lives there; Maneet added as admin member.
4. **Railway setup**: Create Railway project from the repo → add Postgres plugin → set env vars → confirm auto-deploy on push works → set spending cap.
5. **Data migration**: Move historic pairing data from the local DB into Railway Postgres. Verify row counts and that the no-repeat-pairing logic reads history correctly.
6. **Cron**: Configure the weekly pairing job on Railway's scheduler. Do not enable live emails/Slack until Maneet explicitly says so — dry-run mode first (log pairings, send nothing).
7. **Domain (may come later)**: Add custom domain in Railway, create the CNAME wherever yaledebate.org DNS is managed. Requires DNS access Maneet may not have yet — if blocked, the `*.up.railway.app` URL is fine for now.

Auth, the web frontend, and everything in the roadmap comes **after** this pipeline works end to end. The point of doing LunchTag first is to prove the maintenance loop while the app is small.

## Working with Maneet

- Prefers critical, candid feedback — say when something is a bad idea and why.
- Values incremental delivery: small shippable steps over big-bang builds.
- Ask before: any action involving money, sending real emails/messages to members, deleting data, or touching anything owned by the YDA board (their sheets, their accounts).
