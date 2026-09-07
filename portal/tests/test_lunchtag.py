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
    repo.set_lunchtag_opt_in(a.id, True)
    repo.close()
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "ava@yale.edu"
    resp = client.get("/lunchtag")
    assert resp.status_code == 200
    assert b"Ben" in resp.data  # her partner, in the hero card
    assert b"This week's pairing" in resp.data
    assert b"Past pairings" in resp.data


def test_hero_card_shows_partner_profile(auth_env, db_path):
    app = create_app(db_path)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    repo.upsert_profile(a.id, display_name="Ava")
    repo.set_lunchtag_opt_in(a.id, True)
    repo.upsert_profile(
        b.id, display_name="Ben", class_year="2028", residential_college="Morse",
        dining_hall="Commons", fun_facts="Collects rare stamps.",
    )
    repo.close()
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "ava@yale.edu"
    html = client.get("/lunchtag").get_data(as_text=True)
    assert "Class of 2028" in html
    assert "Morse" in html
    assert "Commons" in html
    assert "Collects rare stamps." in html


def test_history_widget_stacks_cards_and_paginates(auth_env, db_path):
    app = create_app(db_path)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    for _ in range(8):
        sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
        repo.confirm_draft(sid, 0.0)
    repo.upsert_profile(a.id, display_name="Ava")
    repo.set_lunchtag_opt_in(a.id, True)
    repo.close()
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "ava@yale.edu"
    html = client.get("/lunchtag").get_data(as_text=True)
    # Hero shows the newest round; sidebar stacks the 7 past ones, 5 max.
    assert 'class="hero-pairing' in html or "hero-pairing" in html
    assert html.count('class="widget-card') == 5
    assert "Load more (2 older)" in html
    assert "show=10" in html
    html = client.get("/lunchtag?show=10").get_data(as_text=True)
    assert html.count('class="widget-card') == 7
    assert "Load more" not in html
    # Bad show values fall back safely.
    assert client.get("/lunchtag?show=banana").status_code == 200


def test_boost_resets_after_pair_happens(auth_env, db_path):
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    c = repo.add_player("Cara")
    repo.add_preference(a.id, b.id, "boost")     # will be paired -> resets
    repo.add_preference(c.id, a.id, "boost")     # not paired -> stays
    repo.add_preference(a.id, b.id, "strike")    # strikes are NOT reset
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    prefs = repo.fetch_preferences()
    pair_ab = (min(a.id, b.id), max(a.id, b.id))
    pair_ca = (min(c.id, a.id), max(c.id, a.id))
    assert pair_ca in prefs.boosted       # untouched boost survives
    assert pair_ab in prefs.forbidden     # strike survives confirmation
    raw = repo.list_preferences(a.id)
    assert raw["boost"] == []             # Ava's boost on Ben was consumed
    repo.close()


def test_lunchtag_page_has_no_profile_edit_form(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "alice@yale.edu", "Alice")
    html = client.get("/lunchtag").get_data(as_text=True)
    assert "Edit profile" not in html
    assert "Fun facts" not in html
    assert "No pairings yet" in html  # empty history state


def test_notification_toggles_default_on_and_save(auth_env, db_path):
    app = create_app(db_path)
    client = onboarded_member(app, "alice@yale.edu", "Alice")
    html = client.get("/lunchtag").get_data(as_text=True)
    assert "Email notifications" in html and "Slack notifications" in html
    assert html.count("checked") == 2  # both toggles on by default
    # Turn email off, keep slack on.
    client.post("/lunchtag/notifications", data={"slack": "1"})
    repo = Repository(db_path)
    pid = store.get_player_id_for_email("alice@yale.edu", db_path=db_path)
    profile = repo.get_profile(pid)
    repo.close()
    assert profile.email_notifications is False
    assert profile.slack_notifications is True
    # Saving the profile form later must not clobber the toggles.
    client.post("/profile/update", data={"display_name": "Alice", "major": "History"})
    repo = Repository(db_path)
    profile = repo.get_profile(pid)
    repo.close()
    assert profile.email_notifications is False
    assert profile.major == "History"


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
    assert "fully anonymous" in html
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


# --- Slack sync (admin) -------------------------------------------------------


def test_slack_sync_buckets_and_links(auth_env, db_path, monkeypatch):
    from portal import slack

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack, "list_members", lambda: [
        {"id": "U1", "name": "Ava Lin", "display_name": "", "email": "ava.lin@yale.edu"},
        {"id": "U2", "name": "Ben Ortiz", "display_name": "benny", "email": "ben@gmail.com"},
        {"id": "U3", "name": "Someone Else", "display_name": "", "email": "else@yale.edu"},
    ])
    app = create_app(db_path)
    repo = Repository(db_path)
    ava = repo.add_player("Ava Lin")
    ben = repo.add_player("Ben Ortiz")
    repo.add_player("Cara Diaz")
    repo.close()
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    admin = app.test_client()
    with admin.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"

    html = admin.get("/lunchtag/admin/slack-sync").get_data(as_text=True)
    assert "Ready to auto-link (1)" in html and "ava.lin@yale.edu" in html
    assert "Needs a yale.edu email from you (1)" in html and "ben@gmail.com" in html
    assert "No Slack match (1)" in html and "Cara Diaz" in html
    assert "Someone Else" not in html  # non-roster Slack members aren't listed

    admin.post("/lunchtag/admin/slack-sync")
    assert store.get_player_id_for_email("ava.lin@yale.edu", db_path=db_path) == ava.id
    assert store.get_player_id_for_email("ben@gmail.com", db_path=db_path) is None  # never auto-linked
    html = admin.get("/lunchtag/admin/slack-sync").get_data(as_text=True)
    assert "Ready to auto-link (0)" in html  # idempotent


def test_slack_sync_page_without_token(auth_env, db_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    app = create_app(db_path)
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    admin = app.test_client()
    with admin.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    resp = admin.get("/lunchtag/admin/slack-sync")
    assert resp.status_code == 200
    assert b"isn't configured" in resp.data
