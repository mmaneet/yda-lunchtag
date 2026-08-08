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
    "major",
    "second_major",
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


def _onboarding_guard(repo):
    """Common checks for the onboarding pages; returns a redirect or None."""
    if not session.get("user_email"):
        return redirect(url_for("index"))
    player = linked_player(repo)
    profile = repo.get_profile(player.id) if player else None
    if profile is not None and profile.onboarded:
        return redirect(url_for("profile.view"))
    return None


@bp.route("/welcome", methods=["GET", "POST"])
def welcome():
    repo = get_repo()
    email = session.get("user_email")
    guard = _onboarding_guard(repo)
    if guard is not None:
        return guard
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            flash("Please tell us what to call you.")
            return redirect(url_for("profile.welcome_setup"))
        player = linked_player(repo)
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
        opted_in = request.form.get("lunchtag_opt_in") == "1"
        repo.set_lunchtag_opt_in(player.id, opted_in)
        flash("Welcome to LunchTag!" if opted_in else "Welcome! You can join LunchTag from your profile anytime.")
        return redirect(url_for("profile.view"))
    return render_template("welcome.html")


@bp.route("/welcome/setup")
def welcome_setup():
    repo = get_repo()
    guard = _onboarding_guard(repo)
    if guard is not None:
        return guard
    player = linked_player(repo)
    return render_template(
        "welcome_setup.html",
        suggested_name=player.name if player else (session.get("user_name") or ""),
        colleges=RESIDENTIAL_COLLEGES,
    )


@bp.route("/profile/lunchtag", methods=["POST"])
def update_lunchtag():
    repo = get_repo()
    player = linked_player(repo)
    if player is None:
        return redirect(url_for("profile.welcome"))
    opted_in = request.form.get("lunchtag_opt_in") == "1"
    repo.set_lunchtag_opt_in(player.id, opted_in)
    flash("You're in — welcome to LunchTag!" if opted_in else "You've opted out of LunchTag.")
    return redirect(url_for("profile.view"))
