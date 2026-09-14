"""One-time script to obtain a Google OAuth2 refresh token for Calendar access.

Run this ONCE, locally on your own machine (NOT inside Docker - it needs to open a
real browser and briefly listen on localhost for the OAuth redirect):

    source venv/bin/activate
    python3 scripts/get_google_refresh_token.py

Prerequisites (one-time, in Google Cloud Console - console.cloud.google.com):
1. Create or select a project.
2. APIs & Services > Library > enable the "Google Calendar API".
3. APIs & Services > Credentials > Create Credentials > OAuth client ID.
   - Application type: Desktop app.
   - Download/copy the Client ID and Client Secret.
4. Put those two values in .env as GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET
   before running this script.

What this does: opens a browser window, asks you to log into the Google account you
want the agent to act as (check availability for / create events on), and asks you to
grant Calendar access. After you approve, it prints a refresh token - paste that into
.env as GOOGLE_OAUTH_REFRESH_TOKEN. That token is reused indefinitely by the app (until
you revoke it from your Google Account's "connected apps" settings) - you only run this
script again if the token gets revoked or you want to connect a different account.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env first, then re-run this.")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency. Run: pip install google-auth-oauthlib")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    print("Opening a browser window - log in with the Google account the agent should act as, "
          "and approve calendar access...\n")

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    credentials = flow.run_local_server(port=0)

    print("\nSuccess. Add this line to .env:\n")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={credentials.refresh_token}\n")
    print("Then restart the api/mcp containers to pick it up:")
    print("  docker compose up -d --force-recreate api mcp\n")


if __name__ == "__main__":
    main()
