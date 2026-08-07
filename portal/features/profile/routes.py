"""Profile feature: member identity — onboarding, viewing and editing your
profile. LunchTag concepts (pairings, strikes, boosts) do not belong here."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from portal.auth import store as auth_store
from portal.shared import get_repo, linked_player

bp = Blueprint("profile", __name__, template_folder="templates")

RESIDENTIAL_COLLEGES = [
    "Benjamin Franklin", "Berkeley", "Branford", "Davenport", "Ezra Stiles",
    "Grace Hopper", "Jonathan Edwards", "Morse", "Pauli Murray", "Pierson",
    "Saybrook", "Silliman", "Timothy Dwight", "Trumbull",
]

PROFILE_FIELDS = (
    "display_name",
    "class_year",
    "residential_college",
    "birthday",
    "dining_hall",
    "fun_facts",
)


@bp.route("/profile")
def view():
    repo = get_repo()
    email = session.get("user_email")
    if not email:
        # Auth disabled (local dev): nothing member-specific to show.
        return render_template("profile/view.html", player=None, profile=None)
    player = linked_player(repo)
    profile = repo.get_profile(player.id) if player else None
    if player is None or profile is None or not profile.onboarded:
        return redirect(url_for("profile.welcome"))
    return render_template(
        "profile/view.html",
        player=player,
        profile=profile,
        colleges=RESIDENTIAL_COLLEGES,
    )


@bp.route("/profile/update", methods=["POST"])
def update():
    repo = get_repo()
    player = linked_player(repo)
    if player is None:
        return redirect(url_for("profile.welcome"))
    fields = {}
    for name in PROFILE_FIELDS:
        if name in request.form:
            fields[name] = request.form.get(name, "").strip() or None
    if fields.get("display_name", "skip") is None:
        flash("Display name can't be empty.")
        return redirect(url_for("profile.view"))
    repo.upsert_profile(player.id, **fields)
    flash("Profile updated.")
    return redirect(url_for("profile.view"))


@bp.route("/welcome", methods=["GET", "POST"])
def welcome():
    repo = get_repo()
    email = session.get("user_email")
    if not email:
        return redirect(url_for("index"))
    player = linked_player(repo)
    profile = repo.get_profile(player.id) if player else None
    if profile is not None and profile.onboarded:
        return redirect(url_for("profile.view"))
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            flash("Please tell us what to call you.")
            return redirect(url_for("profile.welcome"))
        if player is None:
            player = repo.add_player(display_name)
            auth_store.link_player_email(
                email, player.id, db_path=current_app.config["DB_PATH"]
            )
        repo.upsert_profile(
            player.id,
            display_name=display_name,
            class_year=request.form.get("class_year", "").strip() or None,
            residential_college=request.form.get("residential_college", "").strip() or None,
        )
        flash("Welcome to LunchTag!")
        return redirect(url_for("profile.view"))
    return render_template(
        "welcome.html",
        suggested_name=player.name if player else (session.get("user_name") or ""),
        colleges=RESIDENTIAL_COLLEGES,
    )
