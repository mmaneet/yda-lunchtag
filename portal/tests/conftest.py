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


def onboarded_member(app, email, name):
    """Sign a member in and complete onboarding; returns their test client."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = email
    client.post("/welcome", data={"display_name": name})
    return client
