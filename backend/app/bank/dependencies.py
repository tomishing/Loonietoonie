from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.bank.plaid import BankProvider, PlaidClient
from app.bank.token_store import SqliteTokenStore, TokenStore, parse_key
from app.config import Settings, get_settings

NOT_CONFIGURED = "Bank linking is not configured on the server."


def _require_enabled(settings: Settings) -> None:
    if not settings.bank_linking_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)


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
