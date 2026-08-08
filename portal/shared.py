"""Per-request helpers shared by all feature blueprints."""

from __future__ import annotations

from flask import current_app, g, session

from portal.auth import store as auth_store
from portal.storage.repo import Repository


def get_repo() -> Repository:
    if "repo" not in g:
        g.repo = Repository(current_app.config["DB_PATH"])
    return g.repo


def reset_member(email: str, db_path=None) -> bool:
    """Erase a member's player entirely (link, profile, preferences, and any
    pairing memberships) so their next sign-in starts onboarding fresh.
    Used by the auto-resetting onboarding tester accounts; returns True if
    something was deleted."""
    from pathlib import Path

    from portal.config import default_db_path

    path = Path(db_path) if db_path is not None else default_db_path()
    player_id = auth_store.get_player_id_for_email(email, db_path=path)
    if player_id is None:
        return False
    repo = Repository(path)
    try:
        repo.conn.execute("DELETE FROM group_members WHERE player_id = ?", (player_id,))
        repo.conn.execute("DELETE FROM player_emails WHERE player_id = ?", (player_id,))
        repo.conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        repo.conn.commit()
    finally:
        repo.close()
    return True


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
