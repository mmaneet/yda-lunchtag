"""Per-request helpers shared by all feature blueprints."""

from __future__ import annotations

from flask import current_app, g, session

from portal.auth import store as auth_store
from portal.storage.repo import Repository


def get_repo() -> Repository:
    if "repo" not in g:
        g.repo = Repository(current_app.config["DB_PATH"])
    return g.repo


def linked_player(repo: Repository):
    """The Player row linked to the signed-in user's email, or None."""
    email = session.get("user_email")
    if not email:
        return None
    player_id = auth_store.get_player_id_for_email(
        email, db_path=current_app.config["DB_PATH"]
    )
    if player_id is None:
        return None
    try:
        return repo.get_player(player_id)
    except ValueError:
        return None
