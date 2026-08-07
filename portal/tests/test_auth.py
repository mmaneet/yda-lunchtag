from __future__ import annotations

import pytest

from portal.app import create_app
from portal.auth import store
from portal.auth.policy import check_sign_in, evaluate_sign_in
from portal.tests.conftest import onboarded_member

ADMIN_PATHS = (
    "/lunchtag/admin/players",
    "/lunchtag/admin/generate",
    "/lunchtag/admin/history",
    "/lunchtag/admin/reset",
)


# --- graceful degradation: auth disabled -----------------------------------


def test_app_runs_normally_with_auth_disabled(no_auth_env, db_path):
    app = create_app(db_path)
    client = app.test_client()
    assert client.get("/").status_code == 302  # redirects to admin generate
    for path in ADMIN_PATHS:
        assert client.get(path).status_code == 200, path


def test_login_redirects_home_when_auth_disabled(no_auth_env, db_path):
    client = create_app(db_path).test_client()
    resp = client.get("/login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


# --- login gate: auth enabled ----------------------------------------------


def test_gate_requires_login_when_auth_enabled(auth_env, db_path):
    client = create_app(db_path).test_client()
    for path in ADMIN_PATHS + ("/profile", "/lunchtag", "/me"):
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "/login" in resp.headers["Location"], path


def test_login_page_reachable_when_auth_enabled(auth_env, db_path):
    client = create_app(db_path).test_client()
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Sign in with Google" in resp.data


def test_member_blocked_from_admin_endpoints(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "someone@yale.edu", "Someone")
    for path in ADMIN_PATHS:
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert resp.headers["Location"].endswith("/profile"), path
    assert client.get("/profile").status_code == 200
    assert client.get("/lunchtag").status_code == 200


def test_admin_reaches_everything(auth_env, db_path):
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    client = create_app(db_path).test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    for path in ADMIN_PATHS:
        assert client.get(path).status_code == 200, path
    # Admins are members too: their member pages route through onboarding.
    assert client.get("/lunchtag").headers["Location"].endswith("/welcome")


def test_me_redirects_to_lunchtag(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "someone@yale.edu", "Someone")
    resp = client.get("/me")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/lunchtag")


def test_logout_clears_session(auth_env, db_path):
    client = create_app(db_path).test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "someone@yale.edu"
    resp = client.get("/logout")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert client.get("/profile").status_code == 302  # gated again


# --- tab bar ----------------------------------------------------------------


def test_tabs_render_for_member_with_coming_soon(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "someone@yale.edu", "Someone")
    html = client.get("/profile").get_data(as_text=True)
    for label in ("Profile", "LunchTag", "YDBoba", "Events Schedule",
                  "Tournament Registration", "Reimbursements"):
        assert label in html, label
    assert html.count("(Coming Soon!)") == 4
    assert 'class="subnav"' not in html  # no admin sub-nav for members


def test_login_page_has_no_tabs(auth_env, db_path):
    client = create_app(db_path).test_client()
    html = client.get("/login").get_data(as_text=True)
    assert 'class="tabs"' not in html
    assert "Coming Soon" not in html


def test_admin_sees_subnav_on_lunchtag_pages_only(auth_env, db_path):
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    client = create_app(db_path).test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    assert 'class="subnav"' in client.get("/lunchtag/admin/generate").get_data(as_text=True)
    # Profile is not a lunchtag page: no admin sub-nav there.
    client.post("/welcome", data={"display_name": "Boss"})
    assert 'class="subnav"' not in client.get("/profile").get_data(as_text=True)


# --- dummy login ------------------------------------------------------------


def test_dummy_login_disabled_by_default(auth_env, db_path):
    client = create_app(db_path).test_client()
    assert b"Dummy login" not in client.get("/login").data
    resp = client.post("/auth/dummy")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert client.get("/profile").status_code == 302  # still signed out


def test_dummy_login_when_enabled(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("DUMMY_LOGIN_EMAIL", "test@gmail.com")
    client = create_app(db_path).test_client()
    assert b"Dummy login" in client.get("/login").data
    resp = client.post("/auth/dummy")
    assert resp.status_code == 302
    client.post("/welcome", data={"display_name": "Test Dummy"})
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert b"test@gmail.com" in resp.data


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


# --- store: whitelist, settings, roles, links -------------------------------


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
    allowed, _ = check_sign_in("x@yale.edu", email_verified=True, db_path=db_path)
    assert allowed
    store.set_whitelist_enforced(True, db_path=db_path)
    allowed, _ = check_sign_in("x@yale.edu", email_verified=True, db_path=db_path)
    assert not allowed
    store.add_allowed_email("x@yale.edu", db_path=db_path)
    allowed, _ = check_sign_in("x@yale.edu", email_verified=True, db_path=db_path)
    assert allowed


def test_store_roles_roundtrip(db_path):
    assert store.get_role("x@yale.edu", db_path=db_path) == "member"
    store.set_role("X@Yale.EDU", "admin", db_path=db_path)
    assert store.get_role("x@yale.edu", db_path=db_path) == "admin"
    store.set_role("x@yale.edu", "member", db_path=db_path)
    assert store.get_role("x@yale.edu", db_path=db_path) == "member"
    with pytest.raises(ValueError):
        store.set_role("x@yale.edu", "superuser", db_path=db_path)


def test_store_player_email_link_roundtrip(db_path):
    assert store.get_player_id_for_email("a@yale.edu", db_path=db_path) is None
    store.link_player_email("A@yale.edu", 7, db_path=db_path)
    assert store.get_player_id_for_email("a@yale.edu", db_path=db_path) == 7
    store.link_player_email("a@yale.edu", 9, db_path=db_path)  # relink
    assert store.get_player_id_for_email("a@yale.edu", db_path=db_path) == 9
    assert store.emails_by_player(db_path=db_path) == {9: "a@yale.edu"}
    store.unlink_player(9, db_path=db_path)
    assert store.get_player_id_for_email("a@yale.edu", db_path=db_path) is None
