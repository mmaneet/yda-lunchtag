"""Leaderboard feature: who has completed the most LunchTags this semester.

Completions are recorded by the Slack bot when a member posts a photo (or
"had lunchtag") in the LunchTag channel — see portal/features/slack_events.
"""

from __future__ import annotations

from flask import Blueprint, render_template

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
