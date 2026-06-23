"""
YouTube OAuth flow — run once to generate token.json.
After that, all uploads are fully automated.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from config import CLIENT_SECRET, TOKEN_FILE, YOUTUBE_SCOPES


def get_credentials() -> Credentials:
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            print("✓ Token refreshed")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=8080, open_browser=False)
            print("✓ Authorization complete")

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print(f"✓ Token saved → {TOKEN_FILE}")

    return creds


if __name__ == "__main__":
    print("God's Eye — YouTube Auth")
    print("A browser window will open. Sign in and click Allow.\n")
    creds = get_credentials()
    print(f"\n✓ Ready. token.json is saved — future runs are fully automated.")
