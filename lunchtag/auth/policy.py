"""Sign-in policy: who may sign in, decided from data, not code.

Two stages:
1. The email must be a verified yale.edu address (always enforced). The email
   and email_verified flag must come from Google's verified ID token claims —
   never trust the `hd` parameter alone.
2. Optionally, the email must be on the allowed_emails whitelist. Whether the
   whitelist is enforced is a setting in the app_settings table (default: off).
"""

from __future__ import annotations

from collections.abc import Iterable

YALE_DOMAIN = "yale.edu"


def evaluate_sign_in(
    email: str | None,
    *,
    email_verified: bool,
    whitelist_enforced: bool,
    allowed_emails: Iterable[str],
) -> tuple[bool, str]:
    """Pure policy check. Returns (allowed, reason)."""
    email = (email or "").strip().lower()
    if not email or not email_verified:
        return False, "Google did not return a verified email address."
    if not email.endswith("@" + YALE_DOMAIN):
        return False, "Sign-in is restricted to yale.edu accounts."
    if whitelist_enforced and email not in {e.strip().lower() for e in allowed_emails}:
        return False, "This account is not on the member list. Ask a board member to add you."
    return True, "ok"


def check_sign_in(email: str | None, *, email_verified: bool, db_path=None) -> tuple[bool, str]:
    """Policy check backed by the database (whitelist + enforcement toggle)."""
    from lunchtag.auth import store

    return evaluate_sign_in(
        email,
        email_verified=email_verified,
        whitelist_enforced=store.get_whitelist_enforced(db_path=db_path),
        allowed_emails=store.list_allowed_emails(db_path=db_path),
    )
