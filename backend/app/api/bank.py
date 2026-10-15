from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.bank.dependencies import get_bank_provider, get_token_store
from app.bank.plaid import BankProvider, PlaidError
from app.bank.token_store import TokenStore
from app.config import Settings, get_settings
from app.services import bank as service
from app.sheets.schema import utc_now_iso
from app.storage.dependencies import get_repository
from app.storage.models import BankTransaction, BankTransactionFilter
from app.storage.repository import Repository

router = APIRouter(prefix="/bank", tags=["bank"])

UserDep = Annotated[SessionData, Depends(get_current_user)]
RepositoryDep = Annotated[Repository, Depends(get_repository)]
ProviderDep = Annotated[BankProvider, Depends(get_bank_provider)]
StoreDep = Annotated[TokenStore, Depends(get_token_store)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class LinkToken(BaseModel):
    link_token: str


class PublicToken(BaseModel):
    public_token: str = Field(min_length=1, max_length=500)


class BankStatus(BaseModel):
    enabled: bool
    items: list[service.LinkedInstitution]


def _bank_error(exc: PlaidError) -> HTTPException:
    if exc.code in {"INVALID_PUBLIC_TOKEN", "INVALID_INPUT"}:
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail="Linking didn't finish. Please try again.")
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        detail=f"The bank connection service failed ({exc.code}). Please try again.",
    )


@router.get("/items", response_model=BankStatus)
def list_items(user: UserDep, repository: RepositoryDep, settings: SettingsDep) -> BankStatus:
    """Linked banks and cards with their accounts. `enabled` is false when Plaid isn't configured."""
    if not settings.bank_linking_enabled:
        return BankStatus(enabled=False, items=[])
    store = get_token_store(settings)
    return BankStatus(enabled=True, items=service.list_linked(store, repository, user.sub))


@router.post("/link-token", response_model=LinkToken)
def create_link_token(user: UserDep, provider: ProviderDep) -> LinkToken:
    """A short-lived token that opens Plaid Link in the app."""
    try:
        return LinkToken(link_token=service.create_link_token(provider, user.sub))
    except PlaidError as exc:
        raise _bank_error(exc) from exc


@router.post("/items", response_model=service.ItemSyncResult, status_code=status.HTTP_201_CREATED)
def link_item(
    body: PublicToken,
    user: UserDep,
    repository: RepositoryDep,
    provider: ProviderDep,
    store: StoreDep,
    settings: SettingsDep,
) -> service.ItemSyncResult:
    """Finish linking (Plaid Link's public token), save the accounts, and import transactions."""
    try:
        return service.link_item(
            provider,
            store,
            repository,
            user.sub,
            body.public_token,
            repository.list_categories(),
            settings.default_currency,
            utc_now_iso(),
        )
    except PlaidError as exc:
        raise _bank_error(exc) from exc


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_item(
    item_id: str, user: UserDep, repository: RepositoryDep, provider: ProviderDep, store: StoreDep
) -> Response:
    """Unlink a bank: revoke access and delete the token. Imported transactions stay in the Sheet."""
    try:
        service.unlink_item(provider, store, repository, user.sub, item_id)
    except service.ItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Linked bank not found") from exc
    except PlaidError as exc:
        raise _bank_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sync", response_model=service.SyncResult)
def sync(
    user: UserDep, repository: RepositoryDep, provider: ProviderDep, store: StoreDep, settings: SettingsDep
) -> service.SyncResult:
    """Import new, changed, and removed transactions from every linked bank and card."""
    return service.sync_all(
        provider,
        store,
        repository,
        user.sub,
        repository.list_categories(),
        settings.default_currency,
        utc_now_iso(),
    )


@router.get("/transactions", response_model=list[BankTransaction])
def list_transactions(
    user: UserDep,
    repository: RepositoryDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    account_id: str | None = None,
    category: str | None = None,
) -> list[BankTransaction]:
    """Imported bank and card transactions, newest first (read from the Sheet)."""
    return repository.list_bank_transactions(
        BankTransactionFilter(date_from=date_from, date_to=date_to, account_id=account_id, category=category)
    )
