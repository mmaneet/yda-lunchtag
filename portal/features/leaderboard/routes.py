"""Leaderboard feature: who has completed the most LunchTags this semester.

Completions are recorded by the Slack bot when a member posts a photo (or
"had lunchtag") in the LunchTag channel — see portal/features/slack_events.
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, url_for

from portal.shared import get_repo, linked_player

bp = Blueprint("leaderboard", __name__, template_folder="templates")


@bp.route("/leaderboard")
def view():
    repo = get_repo()
    me = linked_player(repo)
    names = repo.display_names()
    rows = []
    rank = 0
    last_count = None
    for i, (player, count) in enumerate(repo.leaderboard(), start=1):
        if count != last_count:
            rank = i  # ties share a rank
            last_count = count
        rows.append({
            "rank": rank,
            "name": names.get(player.id, player.name),
            "count": count,
            "is_me": me is not None and player.id == me.id,
        })
    total = sum(r["count"] for r in rows)
    return render_template("leaderboard/view.html", rows=rows, total=total)


@bp.route("/leaderboard/admin/sync", methods=["POST"])
def admin_sync():
    """Re-scan the Slack channel for missed completions (admin only)."""
    from portal.features.slack_events.routes import backfill_from_channel

    result = backfill_from_channel(db_path=current_app.config["DB_PATH"])
    if result.get("status") == "ok":
        flash(
            f"Slack sync: scanned {result['scanned']} messages, {result['qualifying']} qualifying, "
            f"credited {result['credited']} people, {result['duplicate']} already counted, "
            f"{result['ignored']} ignored (unknown member / not in round)."
        )
    else:
        flash(f"Slack sync did nothing: {result.get('status')}.")
    return redirect(url_for("leaderboard.view"))
