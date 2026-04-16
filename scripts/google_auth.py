#!/usr/bin/env python3
"""
One-time script to obtain a Google OAuth2 refresh token for Calendar API access.

Prerequisites:
1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable the Google Calendar API
3. Create OAuth2 credentials (Application type: Desktop app)
4. Download the client secret JSON file

Usage:
    python scripts/google_auth.py path/to/client_secret.json

The script will open a browser for you to authorise access, then print
the refresh token to add to your .env file.
"""

import argparse
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Install google-auth-oauthlib first: pip install google-auth-oauthlib")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Get Google Calendar refresh token")
    parser.add_argument("client_secrets", help="Path to client_secret.json from Google Cloud")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    print("\n--- Add these to your .env file ---")
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("---")


if __name__ == "__main__":
    main()
