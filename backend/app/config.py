from functools import lru_cache
from pathlib import Path

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

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # Encrypts the session cookie (which holds the Google refresh token). 32+ characters.
    session_secret: str = ""
    session_max_age_days: int = 30
    # Deep link the browser returns to after an Android sign-in.
    app_redirect_uri: str = "com.loonietoonie.app://auth/callback"

    default_currency: str = DEFAULT_CURRENCY

    @property
    def cookie_secure(self) -> bool:
        """Send cookies over HTTPS only, except in local development (plain http://localhost)."""
        return self.app_env != "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
