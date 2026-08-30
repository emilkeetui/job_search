"""Service-account auth. No browser flow, no refresh-token expiry.

PLAN.md section 3: a service account starts with access to nothing and only
ever sees what's explicitly shared with it. Do not switch to user OAuth
without asking the user first.
"""

from __future__ import annotations

from pathlib import Path

from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar.events",
]

DEFAULT_KEY_PATH = Path("credentials/service_account.json")


class AuthError(RuntimeError):
    """Raised when credentials are missing or invalid. CLI should exit 2 on this."""


def load_credentials(key_path: str | Path = DEFAULT_KEY_PATH) -> Credentials:
    key_path = Path(key_path)
    if not key_path.exists():
        raise AuthError(
            f"Service account key not found at {key_path}.\n"
            "Run `joepipe doctor` for setup status, or see README.md for the "
            "Google Cloud service-account walkthrough."
        )
    return Credentials.from_service_account_file(str(key_path), scopes=SCOPES)


def service_account_email(creds: Credentials) -> str:
    return creds.service_account_email
