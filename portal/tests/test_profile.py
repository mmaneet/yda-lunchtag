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
    resp = client.get("/welcome")
    assert resp.status_code == 200
    assert b"Welcome to LunchTag!" in resp.data
    assert b"New Member" in resp.data  # Google name prefilled


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


def test_profile_page_has_no_lunchtag_content(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "alice@yale.edu", "Alice")
    html = client.get("/profile").get_data(as_text=True)
    # Strike/Boost widgets and pairing history belong to the LunchTag tab.
    assert "Strike" not in html.replace("LunchTag", "")
    assert "Boost" not in html
    assert "Your pairings" not in html
