from __future__ import annotations

import pytest

from lunchtag.auth import store
from lunchtag.auth.policy import check_sign_in, evaluate_sign_in
from lunchtag.ui.app import create_app


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.sqlite"


@pytest.fixture
def no_auth_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")


# --- graceful degradation: auth disabled -----------------------------------


def test_app_runs_normally_with_auth_disabled(no_auth_env, db_path):
    app = create_app(db_path)
    client = app.test_client()
    assert client.get("/").status_code == 302  # redirects to /generate as before
    for path in ("/players", "/generate", "/history", "/reset"):
        assert client.get(path).status_code == 200, path


def test_login_redirects_home_when_auth_disabled(no_auth_env, db_path):
    client = create_app(db_path).test_client()
    resp = client.get("/login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


# --- login gate: auth enabled ----------------------------------------------


def test_gate_requires_login_when_auth_enabled(auth_env, db_path):
    client = create_app(db_path).test_client()
    for path in ("/players", "/generate", "/history", "/reset"):
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "/login" in resp.headers["Location"]


def test_login_page_reachable_when_auth_enabled(auth_env, db_path):
    client = create_app(db_path).test_client()
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Sign in with Google" in resp.data


def test_signed_in_user_passes_gate(auth_env, db_path):
    client = create_app(db_path).test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "someone@yale.edu"
    assert client.get("/players").status_code == 200
    assert b"someone@yale.edu" in client.get("/players").data


def test_logout_clears_session(auth_env, db_path):
    client = create_app(db_path).test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "someone@yale.edu"
    resp = client.get("/logout")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert client.get("/players").status_code == 302  # gated again


# --- sign-in policy: yale.edu check ----------------------------------------


def _policy(email, verified=True, enforced=False, allowed=()):
    return evaluate_sign_in(
        email,
        email_verified=verified,
        whitelist_enforced=enforced,
        allowed_emails=allowed,
    )


def test_policy_accepts_yale_email():
    allowed, _ = _policy("maneet.mehta@yale.edu")
    assert allowed


def test_policy_rejects_non_yale_email():
    allowed, reason = _policy("someone@gmail.com")
    assert not allowed
    assert "yale.edu" in reason


def test_policy_rejects_lookalike_domain():
    allowed, _ = _policy("someone@yale.edu.evil.com")
    assert not allowed


def test_policy_rejects_unverified_email():
    allowed, _ = _policy("someone@yale.edu", verified=False)
    assert not allowed


def test_policy_rejects_missing_email():
    allowed, _ = _policy(None)
    assert not allowed


def test_policy_is_case_insensitive():
    allowed, _ = _policy("Someone@Yale.EDU", enforced=True, allowed=["someone@yale.edu"])
    assert allowed


# --- sign-in policy: whitelist toggle --------------------------------------


def test_whitelist_off_allows_any_yale_email():
    allowed, _ = _policy("newcomer@yale.edu", enforced=False, allowed=[])
    assert allowed


def test_whitelist_on_blocks_unlisted_yale_email():
    allowed, reason = _policy("newcomer@yale.edu", enforced=True, allowed=["member@yale.edu"])
    assert not allowed
    assert "member" in reason.lower()


def test_whitelist_on_allows_listed_yale_email():
    allowed, _ = _policy("member@yale.edu", enforced=True, allowed=["member@yale.edu"])
    assert allowed


def test_whitelist_never_overrides_yale_check():
    allowed, _ = _policy("outsider@gmail.com", enforced=True, allowed=["outsider@gmail.com"])
    assert not allowed


# --- store: whitelist + settings tables ------------------------------------


def test_store_whitelist_roundtrip(db_path):
    assert store.list_allowed_emails(db_path=db_path) == []
    store.add_allowed_email("A@yale.edu", db_path=db_path)
    store.add_allowed_email("b@yale.edu", db_path=db_path)
    store.add_allowed_email("a@yale.edu", db_path=db_path)  # duplicate, ignored
    assert store.list_allowed_emails(db_path=db_path) == ["a@yale.edu", "b@yale.edu"]
    store.remove_allowed_email("A@YALE.EDU", db_path=db_path)
    assert store.list_allowed_emails(db_path=db_path) == ["b@yale.edu"]


def test_store_settings_roundtrip(db_path):
    assert store.get_setting("missing", default="x", db_path=db_path) == "x"
    store.set_setting("k", "v1", db_path=db_path)
    store.set_setting("k", "v2", db_path=db_path)  # upsert
    assert store.get_setting("k", db_path=db_path) == "v2"


def test_store_whitelist_toggle_defaults_off(db_path):
    assert store.get_whitelist_enforced(db_path=db_path) is False
    store.set_whitelist_enforced(True, db_path=db_path)
    assert store.get_whitelist_enforced(db_path=db_path) is True
    store.set_whitelist_enforced(False, db_path=db_path)
    assert store.get_whitelist_enforced(db_path=db_path) is False


def test_check_sign_in_reads_db(db_path):
    # Enforcement off: any yale.edu email works.
    allowed, _ = check_sign_in("x@yale.edu", email_verified=True, db_path=db_path)
    assert allowed
    # Turn enforcement on: only whitelisted emails work.
    store.set_whitelist_enforced(True, db_path=db_path)
    allowed, _ = check_sign_in("x@yale.edu", email_verified=True, db_path=db_path)
    assert not allowed
    store.add_allowed_email("x@yale.edu", db_path=db_path)
    allowed, _ = check_sign_in("x@yale.edu", email_verified=True, db_path=db_path)
    assert allowed
