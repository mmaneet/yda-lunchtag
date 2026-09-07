"""LunchTag feature: weekly lunch pairings.

Member routes (plain names): your pairing + history, strikes, boosts.
Admin routes are named admin_* — the auth gate blocks members from any
endpoint whose name starts with "admin_". Follow that convention.
"""

from __future__ import annotations

import random

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from portal import notifications
from portal.auth import store as auth_store
from portal.features.lunchtag.engine.config import DEFAULT_GENERATION, DEFAULT_SCORING
from portal.features.lunchtag.engine.editing import swap_players
from portal.features.lunchtag.engine.generator import generate_best, validate_groups
from portal.features.lunchtag.engine.scoring import repeat_edges, score_groups
from portal.shared import get_repo, linked_player
from portal.storage.repo import Repository

bp = Blueprint("lunchtag", __name__, template_folder="templates")


# --- member routes -----------------------------------------------------------


@bp.route("")
def me():
    repo = get_repo()
    email = session.get("user_email")
    if not email:
        # Auth disabled (local dev): nothing member-specific to show.
        return render_template("lunchtag/me.html", player=None, history=[])
    player = linked_player(repo)
    profile = repo.get_profile(player.id) if player else None
    if player is None or profile is None or not profile.onboarded:
        return redirect(url_for("profile.welcome"))
    if not profile.lunchtag_opt_in:
        return render_template("lunchtag/opted_out.html")
    names = repo.display_names()
    raw_history = repo.player_history(player.id)
    history = [
        {
            "session": summary,
            "partners": [names.get(pid, f"#{pid}") for pid in member_ids if pid != player.id],
        }
        for summary, member_ids in raw_history
    ]
    # This week's partners, with their profiles for the hero card.
    current_partners = []
    if raw_history:
        for pid in raw_history[0][1]:
            if pid != player.id:
                current_partners.append(
                    {"name": names.get(pid, f"#{pid}"), "profile": repo.get_profile(pid)}
                )
    my_prefs = repo.list_preferences(player.id)
    others = sorted(
        (
            (p.id, names.get(p.id, p.name))
            for p in repo.list_active_players()
            if p.id != player.id
        ),
        key=lambda item: item[1].lower(),
    )
    try:
        show = int(request.args.get("show", 5))
    except ValueError:
        show = 5
    show = max(1, min(show, 500))
    return render_template(
        "lunchtag/me.html",
        player=player,
        profile=profile,
        current=history[0] if history else None,
        current_partners=current_partners,
        past=history[1:],
        show=show,
        others=others,
        strikes=[(pid, names.get(pid, f"#{pid}")) for pid in my_prefs["strike"]],
        boost=(
            (my_prefs["boost"][0], names.get(my_prefs["boost"][0], f"#{my_prefs['boost'][0]}"))
            if my_prefs["boost"]
            else None
        ),
    )


def _preference_target(repo: Repository, player):
    """Validate the posted target id; returns it or None (with a flash)."""
    raw = request.form.get("target_id", "")
    try:
        target_id = int(raw)
    except ValueError:
        flash("Pick a player first.")
        return None
    if target_id == player.id:
        flash("You can't pick yourself.")
        return None
    try:
        repo.get_player(target_id)
    except ValueError:
        flash("That player doesn't exist.")
        return None
    return target_id


def _preference_action(kind: str, add: bool):
    repo = get_repo()
    player = linked_player(repo)
    if player is None:
        return redirect(url_for("profile.welcome"))
    target_id = _preference_target(repo, player)
    if target_id is not None:
        if add:
            repo.add_preference(player.id, target_id, kind)
            flash("Strike added." if kind == "strike" else "Boost set.")
        else:
            repo.remove_preference(player.id, target_id, kind)
            flash("Strike removed." if kind == "strike" else "Boost removed.")
    return redirect(url_for("lunchtag.me"))


@bp.route("/strike", methods=["POST"])
def add_strike():
    return _preference_action("strike", add=True)


@bp.route("/strike/remove", methods=["POST"])
def remove_strike():
    return _preference_action("strike", add=False)


@bp.route("/boost", methods=["POST"])
def add_boost():
    return _preference_action("boost", add=True)


@bp.route("/boost/remove", methods=["POST"])
def remove_boost():
    return _preference_action("boost", add=False)


@bp.route("/notifications", methods=["POST"])
def update_notifications():
    repo = get_repo()
    player = linked_player(repo)
    if player is None:
        return redirect(url_for("profile.welcome"))
    repo.set_notification_prefs(
        player.id,
        email=request.form.get("email") == "1",
        slack=request.form.get("slack") == "1",
    )
    flash("Notification settings saved.")
    return redirect(url_for("lunchtag.me"))


# --- admin routes (admin_* names are enforced by the auth gate) --------------


@bp.route("/admin/players")
def admin_players():
    repo = get_repo()
    players = repo.list_players(include_deleted=True)
    linked_emails = auth_store.emails_by_player(db_path=current_app.config["DB_PATH"])
    return render_template("lunchtag/players.html", players=players, linked_emails=linked_emails)


@bp.route("/admin/players/<int:player_id>/email", methods=["POST"])
def admin_link_player_email(player_id: int):
    email = request.form.get("email", "").strip().lower()
    if email:
        auth_store.link_player_email(email, player_id, db_path=current_app.config["DB_PATH"])
        flash("Email linked.")
    else:
        auth_store.unlink_player(player_id, db_path=current_app.config["DB_PATH"])
        flash("Email unlinked.")
    return redirect(url_for("lunchtag.admin_players"))


@bp.route("/admin/players/add", methods=["POST"])
def admin_add_player():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Player name cannot be empty.")
        return redirect(url_for("lunchtag.admin_players"))
    repo = get_repo()
    repo.add_player(name)
    flash(f"Added {name}.")
    return redirect(url_for("lunchtag.admin_players"))


@bp.route("/admin/players/<int:player_id>/rename", methods=["POST"])
def admin_rename_player(player_id: int):
    name = request.form.get("name", "").strip()
    if not name:
        flash("New name cannot be empty.")
        return redirect(url_for("lunchtag.admin_players"))
    repo = get_repo()
    repo.rename_player(player_id, name)
    flash("Player renamed.")
    return redirect(url_for("lunchtag.admin_players"))


@bp.route("/admin/players/<int:player_id>/toggle", methods=["POST"])
def admin_toggle_player(player_id: int):
    active = request.form.get("active") == "1"
    repo = get_repo()
    repo.set_player_active(player_id, active)
    flash("Player status updated.")
    return redirect(url_for("lunchtag.admin_players"))


@bp.route("/admin/players/<int:player_id>/delete", methods=["POST"])
def admin_delete_player(player_id: int):
    repo = get_repo()
    repo.soft_delete_player(player_id)
    flash("Player deleted (soft).")
    return redirect(url_for("lunchtag.admin_players"))


@bp.route("/admin/generate")
def admin_generate():
    repo = get_repo()
    active_players = repo.list_pairing_pool()
    draft = repo.get_draft()
    return render_template(
        "lunchtag/generate.html",
        active_players=active_players,
        draft=draft,
    )


@bp.route("/admin/draft/generate", methods=["POST"])
def admin_generate_draft():
    repo = get_repo()
    active_players = repo.list_pairing_pool()
    if len(active_players) < 2:
        flash("Need at least 2 active players to generate pairings.")
        return redirect(url_for("lunchtag.admin_generate"))
    stats = repo.fetch_pairing_stats()
    prefs = repo.fetch_preferences()
    rng_seed = random.SystemRandom().randint(0, 2**31 - 1)
    draft = generate_best(
        [p.id for p in active_players],
        stats,
        DEFAULT_SCORING,
        DEFAULT_GENERATION,
        seed=rng_seed,
        prefs=prefs,
    )
    repo.clear_drafts()
    session_id = repo.create_draft(draft.seed, draft.score, draft.groups)
    flash("Draft generated.")
    return redirect(url_for("lunchtag.admin_draft_view", session_id=session_id))


def _repeat_pairs(repo, breakdown, stats, players_map):
    repeats = repeat_edges(breakdown.pair_penalties, stats)
    return [
        (
            players_map[a].name,
            players_map[b].name,
            stats.met_count.get((a, b), 0),
            stats.last_met.get((a, b)),
        )
        for a, b in repeats
    ]


@bp.route("/admin/draft/<int:session_id>")
def admin_draft_view(session_id: int):
    repo = get_repo()
    draft = repo.get_draft()
    if draft is None or draft.session_id != session_id:
        flash("Draft not found.")
        return redirect(url_for("lunchtag.admin_generate"))
    active_players = repo.list_pairing_pool()
    players_map = {p.id: p for p in repo.list_players(include_deleted=True)}
    stats = repo.fetch_pairing_stats()
    prefs = repo.fetch_preferences()
    breakdown = score_groups(
        [g.member_ids for g in draft.groups], stats, DEFAULT_SCORING, prefs=prefs
    )
    return render_template(
        "lunchtag/draft.html",
        draft=draft,
        groups=draft.groups,
        players_map=players_map,
        repeat_pairs=_repeat_pairs(repo, breakdown, stats, players_map),
        active_players=active_players,
        score=breakdown.total,
    )


@bp.route("/admin/draft/<int:session_id>/swap", methods=["POST"])
def admin_draft_swap(session_id: int):
    repo = get_repo()
    draft = repo.get_draft()
    if draft is None or draft.session_id != session_id:
        flash("Draft not found.")
        return redirect(url_for("lunchtag.admin_generate"))
    player_a = int(request.form.get("player_a"))
    player_b = int(request.form.get("player_b"))
    if player_a == player_b:
        flash("Select two different players to swap.")
        return redirect(url_for("lunchtag.admin_draft_view", session_id=session_id))
    stats = repo.fetch_pairing_stats()
    prefs = repo.fetch_preferences()
    old_groups = [g.member_ids for g in draft.groups]
    old_breakdown = score_groups(old_groups, stats, DEFAULT_SCORING, prefs=prefs)
    old_repeats = len(repeat_edges(old_breakdown.pair_penalties, stats))
    groups = swap_players(old_groups, player_a, player_b)
    active_ids = [p.id for p in repo.list_pairing_pool()]
    valid, msg = validate_groups(groups, active_ids)
    if not valid:
        flash(f"Invalid edit: {msg}")
        return redirect(url_for("lunchtag.admin_draft_view", session_id=session_id))
    breakdown = score_groups(groups, stats, DEFAULT_SCORING, prefs=prefs)
    new_repeats = len(repeat_edges(breakdown.pair_penalties, stats))
    repo.update_draft(session_id, breakdown.total, groups)
    delta = breakdown.total - old_breakdown.total
    delta_label = f"{delta:+.2f}" if abs(delta) > 0.001 else "0.00"
    repeats_delta = new_repeats - old_repeats
    repeats_label = f"{repeats_delta:+d}" if repeats_delta != 0 else "0"
    flash(f"Players swapped. Score change {delta_label}. Repeat edges change {repeats_label}.")
    return redirect(url_for("lunchtag.admin_draft_view", session_id=session_id))


@bp.route("/admin/draft/<int:session_id>/confirm", methods=["POST"])
def admin_confirm_draft(session_id: int):
    repo = get_repo()
    draft = repo.get_draft()
    if draft is None or draft.session_id != session_id:
        flash("Draft not found.")
        return redirect(url_for("lunchtag.admin_generate"))
    stats = repo.fetch_pairing_stats()
    prefs = repo.fetch_preferences()
    breakdown = score_groups(
        [g.member_ids for g in draft.groups], stats, DEFAULT_SCORING, prefs=prefs
    )
    repo.confirm_draft(session_id, breakdown.total)
    flash("Draft confirmed and saved to history.")
    if notifications.notifications_enabled(current_app.config["DB_PATH"]):
        notifications.send_in_background(
            current_app.config["DB_PATH"], session_id, sync=current_app.config.get("NOTIFY_SYNC", False)
        )
        flash("Notifications are sending in the background — open this round in History to see the summary.")
    else:
        flash("Notifications are off — enable with: python -m portal.cli notifications on")
    return redirect(url_for("lunchtag.admin_history"))


@bp.route("/admin/draft/<int:session_id>/discard", methods=["POST"])
def admin_discard_draft(session_id: int):
    repo = get_repo()
    repo.delete_draft(session_id)
    flash("Draft discarded.")
    return redirect(url_for("lunchtag.admin_generate"))


@bp.route("/admin/history")
def admin_history():
    repo = get_repo()
    sessions = repo.list_confirmed_sessions()
    return render_template("lunchtag/history.html", sessions=sessions)


@bp.route("/admin/history/<int:session_id>")
def admin_history_detail(session_id: int):
    repo = get_repo()
    pairing_session = repo.get_session(session_id)
    players_map = {p.id: p for p in repo.list_players(include_deleted=True)}
    stats = repo.fetch_pairing_stats()
    breakdown = score_groups(
        [g.member_ids for g in pairing_session.groups], stats, DEFAULT_SCORING
    )
    return render_template(
        "lunchtag/session.html",
        session=pairing_session,
        groups=pairing_session.groups,
        players_map=players_map,
        repeat_pairs=_repeat_pairs(repo, breakdown, stats, players_map),
        notification_runs=repo.notification_runs(session_id),
        notifications_on=notifications.notifications_enabled(current_app.config["DB_PATH"]),
    )


@bp.route("/admin/history/<int:session_id>/notify", methods=["POST"])
def admin_resend_notifications(session_id: int):
    """(Re)send notifications for a confirmed round, per channel."""
    repo = get_repo()
    if repo.get_session(session_id).status != "confirmed":
        flash("Only confirmed rounds can be sent.")
        return redirect(url_for("lunchtag.admin_history"))
    channels = [c for c in notifications.ALL_CHANNELS if request.form.get(c) == "1"]
    if not channels:
        flash("Pick at least one channel.")
    elif not notifications.notifications_enabled(current_app.config["DB_PATH"]):
        flash("Notifications are off — enable with: python -m portal.cli notifications on")
    else:
        notifications.send_in_background(
            current_app.config["DB_PATH"], session_id, channels,
            sync=current_app.config.get("NOTIFY_SYNC", False),
        )
        flash(f"Sending {', '.join(channels)} in the background — refresh for the summary.")
    return redirect(url_for("lunchtag.admin_history_detail", session_id=session_id))


@bp.route("/admin/templates", methods=["GET", "POST"])
def admin_templates():
    """Audit and edit every automated message (email, Slack DM, channel post,
    admin reminder)."""
    db = current_app.config["DB_PATH"]
    if request.method == "POST":
        key = request.form.get("key", "")
        if key not in notifications.TEMPLATES:
            flash("Unknown template.")
            return redirect(url_for("lunchtag.admin_templates"))
        if request.form.get("action") == "reset":
            notifications.set_template(key, "", db_path=db)
            flash(f"{notifications.TEMPLATES[key]['label']}: reset to default.")
        else:
            notifications.set_template(key, request.form.get("value", ""), db_path=db)
            flash(f"{notifications.TEMPLATES[key]['label']}: saved.")
        return redirect(url_for("lunchtag.admin_templates", _anchor=key))
    items = []
    for key, spec in notifications.TEMPLATES.items():
        preview, error = notifications.preview_message(key, db_path=db)
        items.append({
            "key": key, "spec": spec,
            "value": notifications.get_template(key, db_path=db),
            "customized": notifications.get_template(key, db_path=db) != spec["default"],
            "preview": preview, "error": error,
        })
    return render_template("lunchtag/templates.html", items=items)


@bp.route("/admin/email-template")
def admin_email_template():
    return redirect(url_for("lunchtag.admin_templates"))


def _slack_sync_report(repo):
    """Match roster players to Slack members by name. Returns buckets; only
    yale.edu matches are eligible for auto-linking."""
    from portal import slack

    members = slack.list_members()
    by_norm: dict[str, list[dict]] = {}
    for m in members:
        for cand in (m["name"], m["display_name"]):
            key = slack.normalize_name(cand)
            if key:
                by_norm.setdefault(key, []).append(m)
    linked_emails = auth_store.emails_by_player(db_path=current_app.config["DB_PATH"])
    known_slack = auth_store.slack_users_by_player(db_path=current_app.config["DB_PATH"])
    report = {"linkable": [], "non_yale": [], "unmatched": [], "already_linked": 0,
              "slack_ids": [], "slack_members": len(members)}
    for player in repo.list_players():
        hits = by_norm.get(slack.normalize_name(player.name), [])
        unique = {m["id"]: m for m in hits}
        # One unambiguous name match is enough to know where to DM them —
        # messaging is low-stakes, unlike login identity (emails below).
        if len(unique) == 1 and player.id not in known_slack:
            m = next(iter(unique.values()))
            report["slack_ids"].append((player, m["id"], m["email"]))
        if player.id in linked_emails:
            report["already_linked"] += 1
            continue
        emails = sorted({m["email"] for m in hits if m["email"]})
        if len(emails) == 1 and emails[0].endswith("@yale.edu"):
            report["linkable"].append((player, emails[0]))
        elif emails:
            report["non_yale"].append((player, emails))
        else:
            report["unmatched"].append(player)
    return report


@bp.route("/admin/slack-sync", methods=["GET", "POST"])
def admin_slack_sync():
    from portal import slack

    repo = get_repo()
    if not slack.enabled():
        return render_template("lunchtag/slack_sync.html", enabled=False, report=None)
    try:
        report = _slack_sync_report(repo)
    except Exception as exc:
        flash(f"Slack lookup failed: {exc}")
        return redirect(url_for("lunchtag.admin_players"))
    if request.method == "POST":
        for player, email in report["linkable"]:
            auth_store.link_player_email(email, player.id, db_path=current_app.config["DB_PATH"])
        for player, slack_id, slack_email in report["slack_ids"]:
            auth_store.set_slack_user(player.id, slack_id, slack_email, db_path=current_app.config["DB_PATH"])
        flash(
            f"Linked {len(report['linkable'])} yale.edu emails and recorded Slack accounts "
            f"for {len(report['slack_ids'])} members (DMs now reach them directly)."
        )
        return redirect(url_for("lunchtag.admin_slack_sync"))
    return render_template("lunchtag/slack_sync.html", enabled=True, report=report)


@bp.route("/admin/reset")
def admin_reset_view():
    return render_template("lunchtag/reset.html")


@bp.route("/admin/reset/history", methods=["POST"])
def admin_reset_history():
    repo = get_repo()
    repo.reset_history()
    flash("Pairing history cleared.")
    return redirect(url_for("lunchtag.admin_history"))


@bp.route("/admin/reset/full", methods=["POST"])
def admin_reset_full():
    repo = get_repo()
    repo.full_reset()
    flash("All data wiped.")
    return redirect(url_for("lunchtag.admin_players"))
