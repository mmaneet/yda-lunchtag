from __future__ import annotations

import pytest


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


def onboarded_member(app, email, name, opt_in=True):
    """Sign a member in and complete onboarding; returns their test client.
    Opted into LunchTag by default (the common case for tests)."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = email
    data = {"display_name": name}
    if opt_in:
        data["lunchtag_opt_in"] = "1"
    client.post("/welcome", data=data)
    return client
