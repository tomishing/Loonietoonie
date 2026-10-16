"""Minimal Plaid client (REST over `requests`): Link tokens, token exchange, accounts, transactions.

Only the endpoints LoonieToonie uses, with Pydantic models for the parts of the responses it
reads. `BankProvider` is the interface the bank service depends on, so tests use a fake.
"""

import datetime as dt
import logging
from decimal import Decimal
from typing import Any, Protocol

import requests
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

PLAID_HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
REQUEST_TIMEOUT_SECONDS = 30
SYNC_PAGE_SIZE = 500
# How far back the first import of a new link goes (Plaid's maximum is 730 days).
DAYS_REQUESTED = 730


class PlaidError(Exception):
    """A Plaid API error. `code` is Plaid's error_code, e.g. ITEM_LOGIN_REQUIRED."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class PlaidAccount(BaseModel):
    account_id: str
    name: str = ""
    official_name: str | None = None
    mask: str | None = None
    type: str = ""
    subtype: str | None = None
    iso_currency_code: str | None = None
    unofficial_currency_code: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PlaidAccount":
        balances = data.get("balances") or {}
        return cls(
            **data,
            iso_currency_code=balances.get("iso_currency_code"),
            unofficial_currency_code=balances.get("unofficial_currency_code"),
        )


class PersonalFinanceCategory(BaseModel):
    primary: str = ""
    detailed: str = ""


class Counterparty(BaseModel):
    name: str = ""
    type: str = ""
    confidence_level: str | None = None


class PlaidTransaction(BaseModel):
    transaction_id: str
    account_id: str
    # Positive = money out of the account (a purchase); negative = money in (refund, payment).
    amount: Decimal
    iso_currency_code: str | None = None
    unofficial_currency_code: str | None = None
    date: dt.date
    authorized_date: dt.date | None = None
    name: str = ""
    merchant_name: str | None = None
    pending: bool = False
    personal_finance_category: PersonalFinanceCategory | None = None
    counterparties: list[Counterparty] = Field(default_factory=list)

    @property
    def merchant_confidence(self) -> str:
        """Plaid's confidence in its merchant match: VERY_HIGH, HIGH, MEDIUM, LOW, UNKNOWN, or ""."""
        merchant = next((c for c in self.counterparties if c.type == "merchant"), None)
        return (merchant.confidence_level or "").upper() if merchant else ""


class SyncPage(BaseModel):
    added: list[PlaidTransaction] = Field(default_factory=list)
    modified: list[PlaidTransaction] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)  # transaction IDs
    next_cursor: str
    has_more: bool


class BankProvider(Protocol):
    def create_link_token(self, user_id: str) -> str: ...

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        """Returns (access_token, item_id)."""
        ...

    def get_institution(self, access_token: str) -> tuple[str, str]:
        """Returns (institution_id, institution_name) for an item; ("", "") if unknown."""
        ...

    def get_accounts(self, access_token: str) -> list[PlaidAccount]: ...

    def sync_transactions(self, access_token: str, cursor: str) -> SyncPage: ...

    def remove_item(self, access_token: str) -> None: ...

    def refresh_transactions(self, access_token: str) -> None:
        """Ask Plaid to read the bank now (it finishes in the background). Billed per request
        outside the Trial plan."""
        ...

    def bank_updated_at(self, access_token: str) -> str:
        """When Plaid last read the bank's transactions successfully (ISO 8601), or ""."""
        ...


class PlaidClient:
    def __init__(self, client_id: str, secret: str, env: str, country_codes: list[str]) -> None:
        if env not in PLAID_HOSTS:
            raise ValueError(f"PLAID_ENV must be one of {sorted(PLAID_HOSTS)}")
        self._host = PLAID_HOSTS[env]
        self._auth = {"client_id": client_id, "secret": secret}
        self._country_codes = country_codes
        self._session = requests.Session()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._session.post(
                self._host + path, json={**self._auth, **body}, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise PlaidError("CONNECTION_ERROR", f"Could not reach Plaid: {exc}") from exc
        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise PlaidError(
                "INVALID_RESPONSE", "Plaid returned a non-JSON response", response.status_code
            ) from exc
        if response.status_code != 200:
            code = str(data.get("error_code") or "PLAID_ERROR")
            logger.warning("Plaid %s failed: %s (request %s)", path, code, data.get("request_id"))
            raise PlaidError(code, str(data.get("error_message") or ""), response.status_code)
        return data

    def create_link_token(self, user_id: str) -> str:
        data = self._post(
            "/link/token/create",
            {
                "client_name": "LoonieToonie",
                "user": {"client_user_id": user_id},
                "products": ["transactions"],
                "transactions": {"days_requested": DAYS_REQUESTED},
                "country_codes": self._country_codes,
                "language": "en",
            },
        )
        return str(data["link_token"])

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        return str(data["access_token"]), str(data["item_id"])

    def get_institution(self, access_token: str) -> tuple[str, str]:
        item = self._post("/item/get", {"access_token": access_token}).get("item") or {}
        institution_id = str(item.get("institution_id") or "")
        if not institution_id:
            return "", ""
        try:
            institution = self._post(
                "/institutions/get_by_id",
                {"institution_id": institution_id, "country_codes": self._country_codes},
            )["institution"]
        except PlaidError:
            return institution_id, ""
        return institution_id, str(institution.get("name") or "")

    def get_accounts(self, access_token: str) -> list[PlaidAccount]:
        data = self._post("/accounts/get", {"access_token": access_token})
        try:
            return [PlaidAccount.from_api(account) for account in data.get("accounts", [])]
        except ValidationError as exc:
            raise PlaidError("INVALID_RESPONSE", "Unexpected accounts data from Plaid") from exc

    def sync_transactions(self, access_token: str, cursor: str) -> SyncPage:
        body: dict[str, Any] = {"access_token": access_token, "count": SYNC_PAGE_SIZE}
        if cursor:
            body["cursor"] = cursor
        data = self._post("/transactions/sync", body)
        try:
            return SyncPage(
                added=data.get("added", []),
                modified=data.get("modified", []),
                removed=[str(r["transaction_id"]) for r in data.get("removed", [])],
                next_cursor=data["next_cursor"],
                has_more=data["has_more"],
            )
        except (ValidationError, KeyError) as exc:
            raise PlaidError("INVALID_RESPONSE", "Unexpected transactions data from Plaid") from exc

    def remove_item(self, access_token: str) -> None:
        self._post("/item/remove", {"access_token": access_token})

    def refresh_transactions(self, access_token: str) -> None:
        self._post("/transactions/refresh", {"access_token": access_token})

    def bank_updated_at(self, access_token: str) -> str:
        status = self._post("/item/get", {"access_token": access_token}).get("status") or {}
        return str((status.get("transactions") or {}).get("last_successful_update") or "")
