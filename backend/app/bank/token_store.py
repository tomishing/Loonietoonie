"""Encrypted server-side store for bank access tokens (storage stage 3, specification.md §8).

Holds only what the server needs to reach a linked bank: the user's Google ID, Plaid item IDs,
the encrypted access tokens, the institution name, and the transactions sync cursor. No financial
data (accounts and transactions go to the user's own spreadsheet). Tokens are encrypted with
AES-256-GCM; the key comes from TOKEN_ENCRYPTION_KEY and never touches the database.
"""

import base64
import binascii
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS plaid_items (
    item_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    institution_id TEXT NOT NULL DEFAULT '',
    institution_name TEXT NOT NULL DEFAULT '',
    access_token BLOB NOT NULL,
    cursor TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_synced_at TEXT NOT NULL DEFAULT '',
    data_version INTEGER NOT NULL DEFAULT 1,
    bank_updated_at TEXT NOT NULL DEFAULT '',
    refreshed_at TEXT NOT NULL DEFAULT '',
    needs_reconnect INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS plaid_items_user ON plaid_items (user_id);
"""


class TokenStoreError(Exception):
    """The token store is misconfigured or a token can't be decrypted (e.g. the key changed)."""


@dataclass(frozen=True)
class LinkedItem:
    item_id: str
    user_id: str
    institution_id: str
    institution_name: str
    access_token: str
    cursor: str
    created_at: str
    last_synced_at: str
    # How the item's transactions were imported; older versions are re-imported once (bank service).
    data_version: int = 1
    # When Plaid last read the bank successfully (Plaid's item status), and when we last asked
    # Plaid to read it right away (/transactions/refresh).
    bank_updated_at: str = ""
    refreshed_at: str = ""
    # The bank asked for a new sign-in (Plaid ITEM_LOGIN_REQUIRED and similar): the app offers
    # Reconnect (Plaid update mode), which keeps the link, its transactions, and its sync cursor.
    needs_reconnect: bool = False


# Columns added after the first release of the table (added on start, existing rows get the default).
ADDED_COLUMNS = {
    "data_version": "INTEGER NOT NULL DEFAULT 1",
    "bank_updated_at": "TEXT NOT NULL DEFAULT ''",
    "refreshed_at": "TEXT NOT NULL DEFAULT ''",
    "needs_reconnect": "INTEGER NOT NULL DEFAULT 0",
}


class TokenStore(Protocol):
    def add_item(
        self,
        user_id: str,
        item_id: str,
        access_token: str,
        institution_id: str,
        institution_name: str,
        now: str,
    ) -> None: ...

    def list_items(self, user_id: str) -> list[LinkedItem]:
        """The user's linked items, oldest first, with decrypted access tokens."""
        ...

    def save_cursor(
        self,
        user_id: str,
        item_id: str,
        cursor: str,
        synced_at: str,
        data_version: int | None = None,
        bank_updated_at: str | None = None,
    ) -> None: ...

    def mark_refreshed(self, user_id: str, item_id: str, at: str) -> None: ...

    def mark_needs_reconnect(self, user_id: str, item_id: str) -> None:
        """The bank asked for a new sign-in; cleared by the next successful sync (save_cursor)."""
        ...

    def delete_item(self, user_id: str, item_id: str) -> bool: ...


def parse_key(value: str) -> bytes:
    """TOKEN_ENCRYPTION_KEY: 32 random bytes, base64url-encoded (padding optional)."""
    try:
        key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenStoreError("TOKEN_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != 32:
        raise TokenStoreError("TOKEN_ENCRYPTION_KEY must be 32 bytes (base64url-encoded)")
    return key


class SqliteTokenStore:
    def __init__(self, path: Path, key: bytes) -> None:
        self._path = path
        self._cipher = AESGCM(key)
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            existing = {row[1] for row in connection.execute("PRAGMA table_info(plaid_items)")}
            for column, definition in ADDED_COLUMNS.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE plaid_items ADD COLUMN {column} {definition}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection per call: commits on success, always closes."""
        connection = sqlite3.connect(self._path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # The user and item IDs are bound to the ciphertext, so a token copied to another row fails.
    def _encrypt(self, user_id: str, item_id: str, token: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._cipher.encrypt(nonce, token.encode(), f"{user_id}:{item_id}".encode())

    def _decrypt(self, user_id: str, item_id: str, blob: bytes) -> str:
        try:
            plain = self._cipher.decrypt(
                blob[:NONCE_BYTES], blob[NONCE_BYTES:], f"{user_id}:{item_id}".encode()
            )
        except InvalidTag as exc:
            raise TokenStoreError(
                "A bank token can't be decrypted; was TOKEN_ENCRYPTION_KEY changed?"
            ) from exc
        return plain.decode()

    def add_item(
        self,
        user_id: str,
        item_id: str,
        access_token: str,
        institution_id: str,
        institution_name: str,
        now: str,
    ) -> None:
        blob = self._encrypt(user_id, item_id, access_token)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO plaid_items"
                " (item_id, user_id, institution_id, institution_name, access_token, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (item_id) DO UPDATE SET access_token = excluded.access_token,"
                " institution_id = excluded.institution_id, institution_name = excluded.institution_name"
                " WHERE plaid_items.user_id = excluded.user_id",
                (item_id, user_id, institution_id, institution_name, blob, now),
            )

    def list_items(self, user_id: str) -> list[LinkedItem]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT item_id, institution_id, institution_name, access_token, cursor, created_at,"
                " last_synced_at, data_version, bank_updated_at, refreshed_at, needs_reconnect"
                " FROM plaid_items WHERE user_id = ? ORDER BY created_at, item_id",
                (user_id,),
            ).fetchall()
        return [
            LinkedItem(
                item_id=item_id,
                user_id=user_id,
                institution_id=institution_id,
                institution_name=institution_name,
                access_token=self._decrypt(user_id, item_id, blob),
                cursor=cursor,
                created_at=created_at,
                last_synced_at=last_synced_at,
                data_version=data_version,
                bank_updated_at=bank_updated_at,
                refreshed_at=refreshed_at,
                needs_reconnect=bool(needs_reconnect),
            )
            for (
                item_id,
                institution_id,
                institution_name,
                blob,
                cursor,
                created_at,
                last_synced_at,
                data_version,
                bank_updated_at,
                refreshed_at,
                needs_reconnect,
            ) in rows
        ]

    def save_cursor(
        self,
        user_id: str,
        item_id: str,
        cursor: str,
        synced_at: str,
        data_version: int | None = None,
        bank_updated_at: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE plaid_items SET cursor = ?, last_synced_at = ?, needs_reconnect = 0,"
                " data_version = COALESCE(?, data_version), bank_updated_at = COALESCE(?, bank_updated_at)"
                " WHERE item_id = ? AND user_id = ?",
                (cursor, synced_at, data_version, bank_updated_at, item_id, user_id),
            )

    def mark_refreshed(self, user_id: str, item_id: str, at: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE plaid_items SET refreshed_at = ? WHERE item_id = ? AND user_id = ?",
                (at, item_id, user_id),
            )

    def mark_needs_reconnect(self, user_id: str, item_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE plaid_items SET needs_reconnect = 1 WHERE item_id = ? AND user_id = ?",
                (item_id, user_id),
            )

    def delete_item(self, user_id: str, item_id: str) -> bool:
        with self._lock, self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM plaid_items WHERE item_id = ? AND user_id = ?", (item_id, user_id)
            ).rowcount
        return deleted > 0
