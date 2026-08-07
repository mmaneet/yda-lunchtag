from __future__ import annotations

import os
import random
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for, g

from lunchtag.auth import auth_enabled, current_role, init_auth
from lunchtag.auth import store as auth_store
from lunchtag.config import default_db_path
from lunchtag.engine.config import DEFAULT_GENERATION, DEFAULT_SCORING
from lunchtag.engine.editing import swap_players
from lunchtag.engine.generator import generate_best, validate_groups
from lunchtag.engine.scoring import repeat_edges, score_groups
from lunchtag.storage.repo import Repository

RESIDENTIAL_COLLEGES = [
    "Benjamin Franklin", "Berkeley", "Branford", "Davenport", "Ezra Stiles",
    "Grace Hopper", "Jonathan Edwards", "Morse", "Pauli Murray", "Pierson",
    "Saybrook", "Silliman", "Timothy Dwight", "Trumbull",
]


def create_app(db_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "lunchtag-dev")
    app.config["DB_PATH"] = db_path or default_db_path()
    init_auth(app)

    def get_repo() -> Repository:
        if "repo" not in g:
            g.repo = Repository(app.config["DB_PATH"])
        return g.repo

    @app.teardown_appcontext
    def close_repo(exception: Exception | None = None) -> None:
        repo = g.pop("repo", None)
        if repo is not None:
            repo.close()

    @app.context_processor
    def inject_draft() -> dict:
        repo = get_repo()
        draft = repo.get_draft()
        return {"draft_nav": draft}

    @app.route("/")
    def index():
        if auth_enabled() and current_role() != "admin":
            return redirect(url_for("my_pairings"))
        return redirect(url_for("generate"))

    def _linked_player(repo: Repository):
        email = session.get("user_email")
        if not email:
            return None
        player_id = auth_store.get_player_id_for_email(email, db_path=app.config["DB_PATH"])
        if player_id is None:
            return None
        try:
            return repo.get_player(player_id)
        except ValueError:
            return None

    @app.route("/me")
    def my_pairings():
        repo = get_repo()
        email = session.get("user_email")
        if not email:
            # Auth disabled (local dev): nothing member-specific to show.
            return render_template("me.html", player=None, profile=None, history=[])
        player = _linked_player(repo)
        profile = repo.get_profile(player.id) if player else None
        if player is None or profile is None or not profile.onboarded:
            return redirect(url_for("welcome"))
        names = repo.display_names()
        history = [
            {
                "session": summary,
                "partners": [names.get(pid, f"#{pid}") for pid in member_ids if pid != player.id],
            }
            for summary, member_ids in repo.player_history(player.id)
        ]
        return render_template(
            "me.html",
            player=player,
            profile=profile,
            history=history,
            colleges=RESIDENTIAL_COLLEGES,
        )

    @app.route("/welcome", methods=["GET", "POST"])
    def welcome():
        repo = get_repo()
        email = session.get("user_email")
        if not email:
            return redirect(url_for("index"))
        player = _linked_player(repo)
        profile = repo.get_profile(player.id) if player else None
        if profile is not None and profile.onboarded:
            return redirect(url_for("my_pairings"))
        if request.method == "POST":
            display_name = request.form.get("display_name", "").strip()
            if not display_name:
                flash("Please tell us what to call you.")
                return redirect(url_for("welcome"))
            if player is None:
                player = repo.add_player(display_name)
                auth_store.link_player_email(email, player.id, db_path=app.config["DB_PATH"])
            repo.upsert_profile(
                player.id,
                display_name=display_name,
                class_year=request.form.get("class_year", "").strip() or None,
                residential_college=request.form.get("residential_college", "").strip() or None,
            )
            flash("Welcome to LunchTag!")
            return redirect(url_for("my_pairings"))
        return render_template(
            "welcome.html",
            suggested_name=player.name if player else (session.get("user_name") or ""),
            colleges=RESIDENTIAL_COLLEGES,
        )

    @app.route("/me/profile", methods=["POST"])
    def update_profile():
        repo = get_repo()
        player = _linked_player(repo)
        if player is None:
            return redirect(url_for("welcome"))
        fields = {}
        for name in ("display_name", "class_year", "residential_college",
                     "birthday", "dining_hall", "fun_facts"):
            if name in request.form:
                fields[name] = request.form.get(name, "").strip() or None
        if fields.get("display_name", "skip") is None:
            flash("Display name can't be empty.")
            return redirect(url_for("my_pairings"))
        repo.upsert_profile(player.id, **fields)
        flash("Profile updated.")
        return redirect(url_for("my_pairings"))

    @app.route("/players")
    def players():
        repo = get_repo()
        players = repo.list_players(include_deleted=True)
        linked_emails = auth_store.emails_by_player(db_path=app.config["DB_PATH"])
        return render_template("players.html", players=players, linked_emails=linked_emails)

    @app.route("/players/<int:player_id>/email", methods=["POST"])
    def link_player_email(player_id: int):
        email = request.form.get("email", "").strip().lower()
        if email:
            auth_store.link_player_email(email, player_id, db_path=app.config["DB_PATH"])
            flash("Email linked.")
        else:
            auth_store.unlink_player(player_id, db_path=app.config["DB_PATH"])
            flash("Email unlinked.")
        return redirect(url_for("players"))

    @app.route("/players/add", methods=["POST"])
    def add_player():
        name = request.form.get("name", "").strip()
        if not name:
            flash("Player name cannot be empty.")
            return redirect(url_for("players"))
        repo = get_repo()
        repo.add_player(name)
        flash(f"Added {name}.")
        return redirect(url_for("players"))

    @app.route("/players/<int:player_id>/rename", methods=["POST"])
    def rename_player(player_id: int):
        name = request.form.get("name", "").strip()
        if not name:
            flash("New name cannot be empty.")
            return redirect(url_for("players"))
        repo = get_repo()
        repo.rename_player(player_id, name)
        flash("Player renamed.")
        return redirect(url_for("players"))

    @app.route("/players/<int:player_id>/toggle", methods=["POST"])
    def toggle_player(player_id: int):
        active = request.form.get("active") == "1"
        repo = get_repo()
        repo.set_player_active(player_id, active)
        flash("Player status updated.")
        return redirect(url_for("players"))

    @app.route("/players/<int:player_id>/delete", methods=["POST"])
    def delete_player(player_id: int):
        repo = get_repo()
        repo.soft_delete_player(player_id)
        flash("Player deleted (soft).")
        return redirect(url_for("players"))

    @app.route("/generate")
    def generate():
        repo = get_repo()
        active_players = repo.list_active_players()
        draft = repo.get_draft()
        return render_template(
            "generate.html",
            active_players=active_players,
            draft=draft,
        )

    @app.route("/draft/generate", methods=["POST"])
    def generate_draft():
        repo = get_repo()
        active_players = repo.list_active_players()
        if len(active_players) < 2:
            flash("Need at least 2 active players to generate pairings.")
            return redirect(url_for("generate"))
        stats = repo.fetch_pairing_stats()
        rng_seed = random.SystemRandom().randint(0, 2**31 - 1)
        draft = generate_best(
            [p.id for p in active_players],
            stats,
            DEFAULT_SCORING,
            DEFAULT_GENERATION,
            seed=rng_seed,
        )
        repo.clear_drafts()
        session_id = repo.create_draft(draft.seed, draft.score, draft.groups)
        flash("Draft generated.")
        return redirect(url_for("draft_view", session_id=session_id))

    @app.route("/draft/<int:session_id>")
    def draft_view(session_id: int):
        repo = get_repo()
        draft = repo.get_draft()
        if draft is None or draft.session_id != session_id:
            flash("Draft not found.")
            return redirect(url_for("generate"))
        active_players = repo.list_active_players()
        players_map = {p.id: p for p in repo.list_players(include_deleted=True)}
        stats = repo.fetch_pairing_stats()
        breakdown = score_groups([g.member_ids for g in draft.groups], stats, DEFAULT_SCORING)
        repeats = repeat_edges(breakdown.pair_penalties, stats)
        repeat_pairs = [
            (
                players_map[a].name,
                players_map[b].name,
                stats.met_count.get((a, b), 0),
                stats.last_met.get((a, b)),
            )
            for a, b in repeats
        ]
        return render_template(
            "draft.html",
            draft=draft,
            groups=draft.groups,
            players_map=players_map,
            repeat_pairs=repeat_pairs,
            active_players=active_players,
            score=breakdown.total,
        )

    @app.route("/draft/<int:session_id>/swap", methods=["POST"])
    def draft_swap(session_id: int):
        repo = get_repo()
        draft = repo.get_draft()
        if draft is None or draft.session_id != session_id:
            flash("Draft not found.")
            return redirect(url_for("generate"))
        player_a = int(request.form.get("player_a"))
        player_b = int(request.form.get("player_b"))
        if player_a == player_b:
            flash("Select two different players to swap.")
            return redirect(url_for("draft_view", session_id=session_id))
        stats = repo.fetch_pairing_stats()
        old_groups = [g.member_ids for g in draft.groups]
        old_breakdown = score_groups(old_groups, stats, DEFAULT_SCORING)
        old_repeats = len(repeat_edges(old_breakdown.pair_penalties, stats))
        groups = swap_players(old_groups, player_a, player_b)
        active_ids = [p.id for p in repo.list_active_players()]
        valid, msg = validate_groups(groups, active_ids)
        if not valid:
            flash(f"Invalid edit: {msg}")
            return redirect(url_for("draft_view", session_id=session_id))
        breakdown = score_groups(groups, stats, DEFAULT_SCORING)
        new_repeats = len(repeat_edges(breakdown.pair_penalties, stats))
        repo.update_draft(session_id, breakdown.total, groups)
        delta = breakdown.total - old_breakdown.total
        delta_label = f"{delta:+.2f}" if abs(delta) > 0.001 else "0.00"
        repeats_delta = new_repeats - old_repeats
        repeats_label = f"{repeats_delta:+d}" if repeats_delta != 0 else "0"
        flash(f"Players swapped. Score change {delta_label}. Repeat edges change {repeats_label}.")
        return redirect(url_for("draft_view", session_id=session_id))

    @app.route("/draft/<int:session_id>/confirm", methods=["POST"])
    def confirm_draft(session_id: int):
        repo = get_repo()
        draft = repo.get_draft()
        if draft is None or draft.session_id != session_id:
            flash("Draft not found.")
            return redirect(url_for("generate"))
        stats = repo.fetch_pairing_stats()
        breakdown = score_groups([g.member_ids for g in draft.groups], stats, DEFAULT_SCORING)
        repo.confirm_draft(session_id, breakdown.total)
        flash("Draft confirmed and saved to history.")
        return redirect(url_for("history"))

    @app.route("/draft/<int:session_id>/discard", methods=["POST"])
    def discard_draft(session_id: int):
        repo = get_repo()
        repo.delete_draft(session_id)
        flash("Draft discarded.")
        return redirect(url_for("generate"))

    @app.route("/history")
    def history():
        repo = get_repo()
        sessions = repo.list_confirmed_sessions()
        return render_template("history.html", sessions=sessions)

    @app.route("/history/<int:session_id>")
    def history_detail(session_id: int):
        repo = get_repo()
        session = repo.get_session(session_id)
        players_map = {p.id: p for p in repo.list_players(include_deleted=True)}
        stats = repo.fetch_pairing_stats()
        breakdown = score_groups([g.member_ids for g in session.groups], stats, DEFAULT_SCORING)
        repeats = repeat_edges(breakdown.pair_penalties, stats)
        repeat_pairs = [
            (
                players_map[a].name,
                players_map[b].name,
                stats.met_count.get((a, b), 0),
                stats.last_met.get((a, b)),
            )
            for a, b in repeats
        ]
        return render_template(
            "session.html",
            session=session,
            groups=session.groups,
            players_map=players_map,
            repeat_pairs=repeat_pairs,
        )

    @app.route("/reset")
    def reset_view():
        return render_template("reset.html")

    @app.route("/reset/history", methods=["POST"])
    def reset_history():
        repo = get_repo()
        repo.reset_history()
        flash("Pairing history cleared.")
        return redirect(url_for("history"))

    @app.route("/reset/full", methods=["POST"])
    def reset_full():
        repo = get_repo()
        repo.full_reset()
        flash("All data wiped.")
        return redirect(url_for("players"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
