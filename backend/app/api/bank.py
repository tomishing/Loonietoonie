from datetime import date
from decimal import Decimal
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
from app.services import bank_categories, matching
from app.sheets.schema import utc_now_iso
from app.storage.dependencies import get_repository
from app.storage.models import BankTransaction, BankTransactionFilter, BankTransactionKind
from app.storage.repository import Repository

router = APIRouter(prefix="/bank", tags=["bank"])

UserDep = Annotated[SessionData, Depends(get_current_user)]
RepositoryDep = Annotated[Repository, Depends(get_repository)]
ProviderDep = Annotated[BankProvider, Depends(get_bank_provider)]
StoreDep = Annotated[TokenStore, Depends(get_token_store)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class LinkToken(BaseModel):
    link_token: str


class LinkTokenRequest(BaseModel):
    # A linked bank to sign in to again (Reconnect); omitted for linking a new bank.
    item_id: str | None = Field(default=None, max_length=200)


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
def create_link_token(
    user: UserDep, provider: ProviderDep, store: StoreDep, body: LinkTokenRequest | None = None
) -> LinkToken:
    """A short-lived token that opens Plaid Link in the app: for a new bank, or with `item_id`,
    for signing in again to a linked bank (Reconnect)."""
    try:
        item_id = body.item_id if body else None
        return LinkToken(link_token=service.create_link_token(provider, store, user.sub, item_id))
    except service.ItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bank link not found") from exc
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
    item_id: str,
    user: UserDep,
    repository: RepositoryDep,
    provider: ProviderDep,
    store: StoreDep,
    remove_transactions: bool = False,
) -> Response:
    """Unlink a bank: revoke access and delete the token. Imported transactions stay in the Sheet
    unless `remove_transactions=true` (they are then marked deleted)."""
    try:
        service.unlink_item(provider, store, repository, user.sub, item_id, remove_transactions)
    except service.ItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Linked bank not found") from exc
    except PlaidError as exc:
        raise _bank_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sync", response_model=service.SyncResult)
def sync(
    user: UserDep,
    repository: RepositoryDep,
    provider: ProviderDep,
    store: StoreDep,
    settings: SettingsDep,
    refresh: bool = False,
) -> service.SyncResult:
    """Import new, changed, and removed transactions from every linked bank and card.

    `refresh=true` (the Sync button) also asks Plaid to read each bank now, at most once an hour
    per bank; the new transactions arrive in a sync shortly after.
    """
    return service.sync_all(
        provider,
        store,
        repository,
        user.sub,
        repository.list_categories(),
        settings.default_currency,
        utc_now_iso(),
        refresh=refresh,
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


class ReceiptCandidate(BaseModel):
    receipt_id: str
    store: str
    purchased_at: date
    total: Decimal
    currency: str
    # True when the automatic rules would accept this receipt for the charge.
    likely: bool


class ReceiptChoice(BaseModel):
    # A receipt ID, or null for "no receipt matches this charge" (remembered).
    receipt_id: str | None


@router.get("/transactions/{transaction_id}/candidates", response_model=list[ReceiptCandidate])
def receipt_candidates(
    transaction_id: str, user: UserDep, repository: RepositoryDep
) -> list[ReceiptCandidate]:
    """Receipts that could explain a charge (same currency, within a week), best first."""
    try:
        found = matching.candidates(repository, transaction_id)
    except matching.MatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [
        ReceiptCandidate(
            receipt_id=c.receipt.receipt_id,
            store=c.receipt.store,
            purchased_at=c.receipt.purchased_at,
            total=c.receipt.total,
            currency=c.receipt.currency,
            likely=c.score >= 0,
        )
        for c in found
    ]


@router.put("/transactions/{transaction_id}/receipt", status_code=status.HTTP_204_NO_CONTENT)
def choose_receipt(
    transaction_id: str, body: ReceiptChoice, user: UserDep, repository: RepositoryDep
) -> Response:
    """Match a charge to a receipt by hand, or mark it as having no receipt."""
    try:
        matching.set_match(repository, transaction_id, body.receipt_id)
    except matching.MatchError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class CategoryChoice(BaseModel):
    kind: BankTransactionKind = "spending"
    category: str = Field(default="", max_length=200)
    subcategory: str = Field(default="", max_length=200)
    # Also every other charge from the same merchant, now and when new ones are imported.
    apply_to_merchant: bool = True


class CategoryChanged(BaseModel):
    changed: int


@router.put("/transactions/{transaction_id}/category", response_model=CategoryChanged)
def choose_category(
    transaction_id: str, body: CategoryChoice, user: UserDep, repository: RepositoryDep
) -> CategoryChanged:
    """Change a charge's category (or mark it as not spending), optionally for the whole merchant."""
    try:
        changed = bank_categories.set_category(
            repository,
            transaction_id,
            body.kind,
            body.category,
            body.subcategory,
            body.apply_to_merchant,
        )
    except bank_categories.TransactionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Transaction not found") from exc
    except bank_categories.CategoryChoiceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return CategoryChanged(changed=changed)
