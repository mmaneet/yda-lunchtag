"""One-time helper: get a Gmail refresh token so the portal can send email
from a personal account (gmail.send scope only — it cannot read mail).

Setup (once, in the same Google Cloud project as the sign-in OAuth client):
  1. APIs & Services -> Library -> enable "Gmail API".
  2. Credentials -> Create Credentials -> OAuth client ID -> type "Desktop app".
     (Desktop clients allow the localhost redirect this script uses.)
  3. Run:  python scripts/get_gmail_token.py <client_id> <client_secret>
     A browser opens; sign in as the account that should send mail
     (e.g. your Yale address) and approve.
  4. Copy the printed values into Railway env vars:
     GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, MAIL_FROM.

The refresh token is printed to your terminal only — never commit it.
"""

from __future__ import annotations

import http.server
import sys
import threading
import urllib.parse
import webbrowser

import requests

SCOPE = "https://www.googleapis.com/auth/gmail.send"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"

code_holder: dict = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code_holder["code"] = (params.get("code") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Done \xe2\x80\x94 you can close this tab.</h2>")

    def log_message(self, *args):  # silence request logging
        pass


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Usage: python scripts/get_gmail_token.py <client_id> <client_secret>")
    client_id, client_secret = sys.argv[1], sys.argv[2]

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print("Opening browser for consent… (sign in as the sending account)")
    webbrowser.open(auth_url)
    thread.join(timeout=300)
    server.server_close()
    code = code_holder.get("code")
    if not code:
        sys.exit("No authorization code received (timed out or denied).")

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()
    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit("No refresh token in response — remove the app's prior grant at "
                 "myaccount.google.com/permissions and rerun.")
    print("\nSet these in Railway (web + cron services):")
    print(f"  GMAIL_CLIENT_ID={client_id}")
    print(f"  GMAIL_CLIENT_SECRET={client_secret}")
    print(f"  GMAIL_REFRESH_TOKEN={refresh}")
    print("  MAIL_FROM=<the account you just signed in with>")


if __name__ == "__main__":
    main()
