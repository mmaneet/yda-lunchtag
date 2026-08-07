from __future__ import annotations

from portal.app import create_app
from portal.auth import store
from portal.storage.repo import Repository
from portal.tests.conftest import onboarded_member


def test_member_lunchtag_page_shows_history_widget(auth_env, db_path):
    app = create_app(db_path)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    repo.upsert_profile(a.id, display_name="Ava")
    repo.close()
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "ava@yale.edu"
    resp = client.get("/lunchtag")
    assert resp.status_code == 200
    assert b"Ben" in resp.data  # her partner, in the widget
    assert b"Your pairings" in resp.data
    assert b"This week" in resp.data


def test_lunchtag_page_has_no_profile_edit_form(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "alice@yale.edu", "Alice")
    html = client.get("/lunchtag").get_data(as_text=True)
    assert "Edit profile" not in html
    assert "Fun facts" not in html
    assert "No pairings yet" in html  # empty history state


def test_member_can_strike_and_remove(auth_env, db_path):
    app = create_app(db_path)
    alice = onboarded_member(app, "alice@yale.edu", "Alice")
    onboarded_member(app, "bob@yale.edu", "Bob")
    onboarded_member(app, "cara@yale.edu", "Cara")
    repo = Repository(db_path)
    ids = {p.name: p.id for p in repo.list_players()}
    repo.close()
    # Multiple strikes allowed.
    alice.post("/lunchtag/strike", data={"target_id": ids["Bob"]})
    alice.post("/lunchtag/strike", data={"target_id": ids["Cara"]})
    repo = Repository(db_path)
    assert len(repo.fetch_preferences().forbidden) == 2
    repo.close()
    html = alice.get("/lunchtag").get_data(as_text=True)
    assert "admins cannot see your strikes" in html
    assert "Bob" in html and "Cara" in html
    alice.post("/lunchtag/strike/remove", data={"target_id": ids["Bob"]})
    repo = Repository(db_path)
    assert len(repo.fetch_preferences().forbidden) == 1
    repo.close()


def test_boost_replaces_previous_boost(auth_env, db_path):
    app = create_app(db_path)
    alice = onboarded_member(app, "alice@yale.edu", "Alice")
    onboarded_member(app, "bob@yale.edu", "Bob")
    onboarded_member(app, "cara@yale.edu", "Cara")
    repo = Repository(db_path)
    ids = {p.name: p.id for p in repo.list_players()}
    repo.close()
    alice.post("/lunchtag/boost", data={"target_id": ids["Bob"]})
    alice.post("/lunchtag/boost", data={"target_id": ids["Cara"]})
    repo = Repository(db_path)
    prefs = repo.fetch_preferences()
    repo.close()
    assert len(prefs.boosted) == 1  # one boost at a time
    a_id, c_id = ids["Alice"], ids["Cara"]
    assert (min(a_id, c_id), max(a_id, c_id)) in prefs.boosted


def test_self_strike_rejected(auth_env, db_path):
    app = create_app(db_path)
    alice = onboarded_member(app, "alice@yale.edu", "Alice")
    repo = Repository(db_path)
    my_id = repo.list_players()[0].id
    repo.close()
    alice.post("/lunchtag/strike", data={"target_id": my_id})
    repo = Repository(db_path)
    assert not repo.fetch_preferences().forbidden
    repo.close()


def test_admin_pages_never_mention_strikes(auth_env, db_path):
    app = create_app(db_path)
    alice = onboarded_member(app, "alice@yale.edu", "Alice")
    onboarded_member(app, "bob@yale.edu", "Bob")
    repo = Repository(db_path)
    ids = {p.name: p.id for p in repo.list_players()}
    repo.close()
    alice.post("/lunchtag/strike", data={"target_id": ids["Bob"]})
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    admin = app.test_client()
    with admin.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    for path in ("/lunchtag/admin/players", "/lunchtag/admin/generate", "/lunchtag/admin/history"):
        html = admin.get(path).get_data(as_text=True).lower()
        # "lunchtag" contains neither word; strip tab labels' safe text first.
        assert "strike" not in html, path
        assert "boost" not in html, path
