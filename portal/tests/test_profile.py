from __future__ import annotations

from portal.app import create_app
from portal.auth import store
from portal.tests.conftest import onboarded_member


def test_first_login_gets_onboarding(auth_env, db_path):
    client = create_app(db_path).test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "new@yale.edu"
        sess["user_name"] = "New Member"
    resp = client.get("/profile")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/welcome")
    # Step 1: the welcome splash.
    resp = client.get("/welcome")
    assert resp.status_code == 200
    assert b"Welcome to YDA Member Resources!" in resp.data
    assert b"Get started" in resp.data
    # Step 2: the setup form, with the Google name prefilled and the opt-in.
    resp = client.get("/welcome/setup")
    assert resp.status_code == 200
    assert b"New Member" in resp.data
    assert b"Join LunchTag" in resp.data
    assert b"lunchtag_opt_in" in resp.data


def test_onboarding_creates_player_and_profile(auth_env, db_path):
    app = create_app(db_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "new@yale.edu"
    resp = client.post(
        "/welcome",
        data={"display_name": "Newbie", "class_year": "2029", "residential_college": "Berkeley"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")  # lands on Profile tab
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert b"Newbie" in resp.data
    assert b"Class of 2029" in resp.data
    assert b"Berkeley" in resp.data
    player_id = store.get_player_id_for_email("new@yale.edu", db_path=db_path)
    assert player_id is not None
    assert client.get("/welcome").status_code == 302  # onboarding is once


def test_member_can_update_optional_profile_fields(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "new@yale.edu", "Newbie")
    resp = client.post(
        "/profile/update",
        data={
            "display_name": "Newbie",
            "birthday": "March 14",
            "dining_hall": "Berkeley",
            "fun_facts": "I once debated for 9 hours straight.",
        },
    )
    assert resp.status_code == 302
    resp = client.get("/profile")
    assert b"March 14" in resp.data
    assert b"9 hours straight" in resp.data


def test_edit_profile_collapsed_by_default(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "alice@yale.edu", "Alice")
    html = client.get("/profile").get_data(as_text=True)
    assert "<details" in html
    assert "Edit profile" in html
    assert "<details open" not in html


def test_logout_lives_on_profile_tab_only(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "alice@yale.edu", "Alice")
    profile_html = client.get("/profile").get_data(as_text=True)
    assert "Log out" in profile_html
    assert "alice@yale.edu" in profile_html
    # No auth chip in the shared header: other pages have no logout link.
    lunchtag_html = client.get("/lunchtag").get_data(as_text=True)
    assert "Log out" not in lunchtag_html
    assert 'class="auth-chip"' not in lunchtag_html


def test_profile_page_has_no_lunchtag_content(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "alice@yale.edu", "Alice")
    html = client.get("/profile").get_data(as_text=True)
    # Strike/Boost widgets and pairing history belong to the LunchTag tab.
    assert "Strike" not in html.replace("LunchTag", "")
    assert "Boost" not in html
    assert "Your pairings" not in html


# --- LunchTag opt-in ---------------------------------------------------------


def test_opted_out_member_sees_oops_page(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "shy@yale.edu", "Shy Person", opt_in=False)
    resp = client.get("/lunchtag")
    assert resp.status_code == 200
    assert b"Oops! You aren't opted into LunchTag!" in resp.data
    assert b"we'd love to have you" in resp.data.lower()
    assert b"Strike" not in resp.data  # no dashboard widgets


def test_profile_toggle_opts_in_and_out(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "shy@yale.edu", "Shy Person", opt_in=False)
    html = client.get("/profile").get_data(as_text=True)
    assert "not in LunchTag yet" in html
    client.post("/profile/lunchtag", data={"lunchtag_opt_in": "1"})
    html = client.get("/profile").get_data(as_text=True)
    assert "opted in" in html
    assert client.get("/lunchtag").status_code == 200
    assert b"This week's pairing" in client.get("/lunchtag").data
    # And back out again.
    client.post("/profile/lunchtag", data={})
    assert b"Oops!" in client.get("/lunchtag").data


def test_opted_out_members_excluded_from_pairing_pool(auth_env, db_path):
    app = create_app(db_path)
    onboarded_member(app, "a@yale.edu", "Ay")
    onboarded_member(app, "b@yale.edu", "Bee")
    onboarded_member(app, "c@yale.edu", "Cee", opt_in=False)
    from portal.storage.repo import Repository

    repo = Repository(db_path)
    pool_names = {p.name for p in repo.list_pairing_pool()}
    repo.close()
    assert pool_names == {"Ay", "Bee"}


def test_preprovisioned_member_keeps_opt_in_through_onboarding(auth_env, db_path):
    """Admin-imported roster members arrive opted in but not onboarded; the
    onboarding switch must reflect that so submitting doesn't opt them out."""
    from portal.storage.repo import Repository

    repo = Repository(db_path)
    p = repo.add_player("Returning Member")
    repo.set_lunchtag_opt_in(p.id, True)  # minimal profile, onboarded=0
    repo.close()
    store.link_player_email("ret@yale.edu", p.id, db_path=db_path)
    client = create_app(db_path).test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "ret@yale.edu"
    assert client.get("/profile").headers["Location"].endswith("/welcome")  # still onboards
    html = client.get("/welcome/setup").get_data(as_text=True)
    assert 'name="lunchtag_opt_in" value="1" checked' in html
    assert "Returning Member" in html  # existing player name suggested
    client.post("/welcome", data={"display_name": "Returning Member", "lunchtag_opt_in": "1"})
    repo = Repository(db_path)
    assert repo.get_profile(p.id).lunchtag_opt_in is True
    assert len(repo.list_players()) == 1  # reused, not duplicated
    repo.close()
