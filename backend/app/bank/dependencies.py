from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.dependencies import get_current_user, is_app_request
from app.auth.session import SessionData
from app.bank.plaid import BankProvider, PlaidClient
from app.bank.token_store import SqliteTokenStore, TokenStore, parse_key
from app.config import Settings, get_settings

NOT_CONFIGURED = "Bank linking is not configured on the server."
NOT_AVAILABLE = "Bank linking isn't available for this account. Import a statement from your bank instead."


def _require_enabled(settings: Settings) -> None:
    if not settings.bank_linking_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)


def plaid_available(request: Request, user: SessionData, settings: Settings) -> bool:
    """Plaid is offered only on the web app, to allowlisted Google accounts (phase 12)."""
    return not is_app_request(request) and settings.plaid_allowed_for(user.email)


def require_plaid_user(
    request: Request,
    user: Annotated[SessionData, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionData:
    """The signed-in user, if they may link and sync banks; 403 otherwise."""
    if not plaid_available(request, user, settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=NOT_AVAILABLE)
    return user


@lru_cache
def _plaid_client(client_id: str, secret: str, env: str, country_codes: tuple[str, ...]) -> PlaidClient:
    return PlaidClient(client_id, secret, env, list(country_codes))


@lru_cache
def _token_store(path: Path, key: str) -> SqliteTokenStore:
    return SqliteTokenStore(path, parse_key(key))


def get_bank_provider(settings: Annotated[Settings, Depends(get_settings)]) -> BankProvider:
    _require_enabled(settings)
    return _plaid_client(
        settings.plaid_client_id,
        settings.plaid_secret,
        settings.plaid_env,
        tuple(settings.plaid_country_codes),
    )


def get_token_store(settings: Annotated[Settings, Depends(get_settings)]) -> TokenStore:
    _require_enabled(settings)
    return _token_store(settings.token_store_path, settings.token_encryption_key)
