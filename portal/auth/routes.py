"""Google OAuth sign-in (OpenID Connect via Authlib).

Graceful degradation: if the GOOGLE_CLIENT_ID env var is unset, auth is
disabled and the app behaves exactly as before — no login required, all pages
open. When it is set, every route requires a signed-in user except /login,
the /auth/* routes, and /static.
"""

from __future__ import annotations

import os

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from portal.auth import store
from portal.auth.policy import check_sign_in

bp = Blueprint("auth", __name__)

_OAUTH_CLIENT_KEY = "lunchtag_google_oauth"

# Blueprints whose endpoints signed-in non-admins may reach, deny-by-default.
# Within a member blueprint, endpoints named admin_* stay admin-only — new
# features must follow that naming convention for their admin routes.
MEMBER_BLUEPRINTS = {"profile", "lunchtag", "leaderboard"}
MEMBER_APP_ENDPOINTS = {"index", "me_redirect"}


def _member_may_access(endpoint: str) -> bool:
    if endpoint in MEMBER_APP_ENDPOINTS:
        return True
    blueprint, _, name = endpoint.rpartition(".")
    return blueprint in MEMBER_BLUEPRINTS and not name.startswith("admin_")


def auth_enabled() -> bool:
    return bool(current_app.config.get("GOOGLE_CLIENT_ID"))


def current_role() -> str:
    """'admin', 'member', or 'anonymous'. With auth disabled, everyone is admin
    (local dev keeps full access)."""
    if not auth_enabled():
        return "admin"
    email = session.get("user_email")
    if not email:
        return "anonymous"
    if "user_role" not in g:
        g.user_role = store.get_role(email, db_path=current_app.config.get("DB_PATH"))
    return g.user_role


def init_auth(app: Flask) -> None:
    """Register the auth blueprint and (when configured) the login gate."""
    # Authlib reads GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from app.config.
    app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "")
    app.config["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    # TESTING BACKDOOR, off unless the env var is set: shows a "dummy login"
    # control that signs in as one of these comma-separated emails with no
    # OAuth and no yale.edu check. Anyone who can reach the site can use it —
    # unset the env var to kill it.
    app.config["DUMMY_LOGIN_EMAIL"] = os.environ.get("DUMMY_LOGIN_EMAIL", "")
    app.register_blueprint(bp)

    if app.config["GOOGLE_CLIENT_ID"]:
        # Behind Railway's proxy, trust X-Forwarded-* so the OAuth redirect
        # URI is generated as https://<host>/auth/callback, not http://.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

        oauth = OAuth(app)
        app.extensions[_OAUTH_CLIENT_KEY] = oauth.register(
            name="google",
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    @app.before_request
    def require_login():
        if not auth_enabled():
            return None
        endpoint = request.endpoint
        if endpoint is None:  # e.g. 404 — let Flask handle it
            return None
        if endpoint == "static" or endpoint.startswith("auth."):
            return None
        if endpoint.startswith("slack."):
            # Inbound Slack webhooks: no session, authenticated by Slack's
            # request signature inside the handler instead.
            return None
        if not session.get("user_email"):
            return redirect(url_for("auth.login"))
        # Signed in: members only get member pages; admins get everything.
        if current_role() != "admin" and not _member_may_access(endpoint):
            return redirect(url_for("profile.view"))
        return None

    @app.context_processor
    def inject_auth_context():
        return {
            "auth_on": auth_enabled(),
            "user_email": session.get("user_email"),
            "is_admin": current_role() == "admin",
        }


def _google_client():
    return current_app.extensions[_OAUTH_CLIENT_KEY]


@bp.route("/login")
def login():
    if not auth_enabled() or session.get("user_email"):
        return redirect(url_for("index"))
    return render_template("login.html")


@bp.route("/auth/google")
def google_login():
    if not auth_enabled():
        return redirect(url_for("index"))
    redirect_uri = url_for("auth.callback", _external=True)
    # hd is only a login hint (pre-selects the Yale account chooser); the
    # real domain check happens on the verified ID token claims in callback().
    return _google_client().authorize_redirect(redirect_uri, hd="yale.edu")


@bp.route("/auth/callback")
def callback():
    if not auth_enabled():
        return redirect(url_for("index"))
    try:
        token = _google_client().authorize_access_token()
    except Exception:
        flash("Sign-in failed. Please try again.")
        return redirect(url_for("auth.login"))
    claims = token.get("userinfo") or {}
    email = (claims.get("email") or "").strip().lower()
    allowed, reason = check_sign_in(
        email,
        email_verified=bool(claims.get("email_verified")),
        db_path=current_app.config.get("DB_PATH"),
    )
    if not allowed:
        flash(reason)
        return redirect(url_for("auth.login"))
    session.clear()
    session["user_email"] = email
    session["user_name"] = claims.get("name") or ""
    return redirect(url_for("index"))


def dummy_emails() -> list[str]:
    raw = current_app.config.get("DUMMY_LOGIN_EMAIL", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


@bp.route("/auth/dummy", methods=["POST"])
def dummy_login():
    emails = dummy_emails()
    if not emails:
        return redirect(url_for("auth.login"))
    email = (request.form.get("email") or emails[0]).strip().lower()
    if email not in emails:
        flash("Unknown dummy user.")
        return redirect(url_for("auth.login"))
    if email.split("@")[0].startswith("onboard"):
        # Onboarding tester accounts reset on every login, so the fresh
        # first-sign-in flow can be replayed endlessly.
        from portal.shared import reset_member

        reset_member(email, db_path=current_app.config.get("DB_PATH"))
    session.clear()
    session["user_email"] = email
    session["user_name"] = "Dummy user"
    return redirect(url_for("index"))


@bp.route("/logout")
def logout():
    session.clear()
    if auth_enabled():
        return redirect(url_for("auth.login"))
    return redirect(url_for("index"))
