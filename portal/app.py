"""YDA member portal — application factory.

Each feature is a blueprint package under portal/features/<name>/ with its own
routes and templates. To add a feature: copy an existing feature package,
register its blueprint here, and add a TABS entry. Disabled TABS entries are
hidden from the tab bar until the feature ships.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, g, redirect, request, url_for

from portal.auth import auth_enabled, current_role, init_auth
from portal.config import default_db_path
from portal.features.leaderboard.routes import bp as leaderboard_bp
from portal.features.lunchtag.routes import bp as lunchtag_bp
from portal.features.profile.routes import bp as profile_bp
from portal.features.slack_events.routes import bp as slack_bp
from portal.shared import get_repo

TABS = [
    {"label": "Profile", "endpoint": "profile.view", "blueprint": "profile", "enabled": True},
    {"label": "LunchTag", "endpoint": "lunchtag.me", "blueprint": "lunchtag", "enabled": True},
    {"label": "Leaderboard", "endpoint": "leaderboard.view", "blueprint": "leaderboard", "enabled": True},
    {"label": "YDBoba", "enabled": False},
    {"label": "Events Schedule", "enabled": False},
    {"label": "Tournament Registration", "enabled": False},
    {"label": "Reimbursements", "enabled": False},
]


def create_app(db_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "lunchtag-dev")
    app.config["DB_PATH"] = db_path or default_db_path()
    # Notification sends run in a background thread; NOTIFY_SYNC=1 runs them
    # inline (tests, debugging).
    app.config["NOTIFY_SYNC"] = os.environ.get("NOTIFY_SYNC") == "1"
    init_auth(app)
    app.register_blueprint(profile_bp)
    app.register_blueprint(lunchtag_bp, url_prefix="/lunchtag")
    app.register_blueprint(leaderboard_bp)
    app.register_blueprint(slack_bp)

    @app.teardown_appcontext
    def close_repo(exception: Exception | None = None) -> None:
        repo = g.pop("repo", None)
        if repo is not None:
            repo.close()

    @app.context_processor
    def inject_shell_context() -> dict:
        draft = get_repo().get_draft()
        return {
            "draft_nav": draft,
            "tabs": TABS,
            "active_blueprint": request.blueprint,
        }

    @app.route("/")
    def index():
        if auth_enabled() and current_role() != "admin":
            return redirect(url_for("profile.view"))
        return redirect(url_for("lunchtag.admin_generate"))

    @app.route("/me")
    def me_redirect():
        # Old bookmark-friendly member URL.
        return redirect(url_for("lunchtag.me"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
