"""
Interactive Lightspeed re-authorization helper.

The purchase-order features need the `employee:purchase_orders` scope, which is
granted at the OAuth *authorization* step — not at token exchange. The existing
refresh token was authorized only for inventory, so it must be re-authorized
with BOTH scopes (re-requesting employee:inventory too, or the existing
reorder-point writeback would lose access).

Usage:
    python3 reauthorize_lightspeed.py

Steps performed:
  1. Prints the authorization URL (open it in a browser, approve access).
  2. Lightspeed redirects to your app's registered redirect URI with ?code=...
     Paste that code here.
  3. Exchanges the code for a new refresh token and offers to update .env.

PRODUCTION NOTE: the running app reads LIGHTSPEED_REFRESH_TOKEN from its
environment (e.g. a Render env var). Updating .env only affects local runs —
copy the new refresh token into the production environment as well.
"""

import os
import sys
from urllib.parse import quote

from dotenv import load_dotenv
import requests

load_dotenv()

from app.services.lightspeed_client import (
    LIGHTSPEED_SCOPES,
    LIGHTSPEED_AUTHORIZE_URL,
    LIGHTSPEED_TOKEN_URL,
)

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def build_authorize_url(client_id: str) -> str:
    # R-Series documents scopes joined by '+' with literal colons,
    # e.g. scope=employee:inventory+employee:purchase_orders
    scope_str = "+".join(LIGHTSPEED_SCOPES)
    url = (
        f"{LIGHTSPEED_AUTHORIZE_URL}?response_type=code"
        f"&client_id={quote(client_id, safe='')}"
        f"&scope={scope_str}"
    )
    redirect_uri = os.getenv("LIGHTSPEED_REDIRECT_URI")
    if redirect_uri:
        url += f"&redirect_uri={quote(redirect_uri, safe='')}"
    return url


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    payload = {
        "code": code.strip(),
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
    }
    redirect_uri = os.getenv("LIGHTSPEED_REDIRECT_URI")
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri
    resp = requests.post(LIGHTSPEED_TOKEN_URL, data=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def update_env_refresh_token(refresh_token: str):
    with open(ENV_PATH, "r") as f:
        lines = f.readlines()
    found = False
    with open(ENV_PATH, "w") as f:
        for line in lines:
            if line.startswith("LIGHTSPEED_REFRESH_TOKEN"):
                f.write(f'LIGHTSPEED_REFRESH_TOKEN="{refresh_token}"\n')
                found = True
            else:
                f.write(line)
        if not found:
            f.write(f'LIGHTSPEED_REFRESH_TOKEN="{refresh_token}"\n')


def main():
    client_id = os.getenv("LIGHTSPEED_CLIENT_ID")
    client_secret = os.getenv("LIGHTSPEED_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("ERROR: LIGHTSPEED_CLIENT_ID / LIGHTSPEED_CLIENT_SECRET not set in environment.")
        sys.exit(1)

    print("=== Lightspeed Re-Authorization ===\n")
    print("Requesting scopes: " + ", ".join(LIGHTSPEED_SCOPES) + "\n")
    print("1. Open this URL in a browser and approve access:\n")
    print("   " + build_authorize_url(client_id) + "\n")
    print("2. After approving, Lightspeed redirects to your registered redirect URI")
    print("   with a `?code=...` query parameter. Copy that code value.\n")

    code = input("Paste the authorization code here: ").strip()
    if not code:
        print("No code provided. Aborting.")
        sys.exit(1)

    print("\nExchanging code for a refresh token...")
    try:
        data = exchange_code(code, client_id, client_secret)
    except requests.HTTPError as e:
        print(f"FAILED to exchange code: {e.response.text if e.response else e}")
        sys.exit(1)

    refresh_token = data.get("refresh_token")
    access_token = data.get("access_token")
    if not refresh_token:
        print(f"No refresh_token in response: {data}")
        sys.exit(1)

    print("\nSUCCESS.")
    print(f"  Access token:  {access_token[:10]}... (short-lived)")
    print(f"  Refresh token: {refresh_token}\n")

    answer = input("Update local .env with the new refresh token? [y/N]: ").strip().lower()
    if answer == "y":
        update_env_refresh_token(refresh_token)
        print(f"Updated {ENV_PATH}")
    else:
        print("Left .env unchanged.")

    print("\nIMPORTANT: also set LIGHTSPEED_REFRESH_TOKEN in your production environment")
    print("(e.g. the Render env var), then verify with: GET /api/health/lightspeed-po")


if __name__ == "__main__":
    main()
