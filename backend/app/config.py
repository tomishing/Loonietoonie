from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Used when a receipt has no currency. Users can change it per receipt on the review screen.
DEFAULT_CURRENCY = "CAD"

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Absolute paths so settings load the same way whatever the working directory.
# backend/.env is listed last, so it overrides the repo-root .env.
ENV_FILES = (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env")


class Settings(BaseSettings):
    """App settings, loaded from environment variables, backend/.env, or the repo-root .env."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    # https://localhost is the Android app's WebView (Capacitor).
    frontend_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "https://localhost"]
    )
    # Where the browser is sent after Google Sign-In finishes.
    frontend_url: str = "http://localhost:5173"

    anthropic_api_key: str = ""
    anthropic_receipt_model: str = "claude-haiku-5-5"
    anthropic_chat_model: str = "claude-opus-5-5"
    # Claude calls each user may make in any 24 hours (0 turns a limit off): receipt scan
    # requests (an image retry counts too), Ask questions, and statement imports.
    daily_scan_limit: int = 40
    daily_ask_limit: int = 20
    daily_import_limit: int = 20

    def daily_limit(self, feature: Literal["scan", "ask", "import"]) -> int:
        return {
            "scan": self.daily_scan_limit,
            "ask": self.daily_ask_limit,
            "import": self.daily_import_limit,
        }[feature]

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # Encrypts the session cookie (which holds the Google refresh token). 32+ characters.
    session_secret: str = ""
    session_max_age_days: int = 30
    # Deep link the browser returns to after an Android sign-in.
    app_redirect_uri: str = "com.loonietoonie.app://auth/callback"
    # Google callback for Android app sign-ins, when the app reaches the backend at a different
    # address than the website (e.g. https://laptop.tailnet.ts.net/api/auth/callback while the
    # website uses localhost). Empty: same as GOOGLE_REDIRECT_URI.
    app_google_redirect_uri: str = ""

    def google_callback(self, for_app: bool) -> str:
        return (
            (self.app_google_redirect_uri or self.google_redirect_uri)
            if for_app
            else self.google_redirect_uri
        )

    default_currency: str = DEFAULT_CURRENCY

    # Bank and card linking (Plaid). Empty client ID or secret turns the feature off.
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"  # sandbox | production
    plaid_country_codes: list[str] = Field(default_factory=lambda: ["CA", "US"])
    # Encrypted token store (storage stage 3): only Plaid access tokens and sync cursors, no
    # financial data. TOKEN_ENCRYPTION_KEY is 32 random bytes, base64url-encoded.
    token_store_path: Path = BACKEND_DIR / "data" / "tokens.sqlite3"
    token_encryption_key: str = ""
    # Google emails that may link banks through Plaid on the web app (phase 12): the developer's
    # household. Empty: nobody. The Android app never offers Plaid.
    plaid_allowed_emails: list[str] = Field(default_factory=list)

    @property
    def bank_linking_enabled(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret and self.token_encryption_key)

    def plaid_allowed_for(self, email: str) -> bool:
        """Whether this Google account is on the Plaid allowlist (case-insensitive)."""
        allowed = {address.strip().casefold() for address in self.plaid_allowed_emails}
        return self.bank_linking_enabled and email.strip().casefold() in allowed

    @property
    def cookie_secure(self) -> bool:
        """Send cookies over HTTPS only, except in local development (plain http://localhost)."""
        return self.app_env != "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
