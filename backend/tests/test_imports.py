"""Statement import (phase 11): parsing bank files, duplicates, categories, and the API.

Sample files are shaped like Canadian banks' exports, with made-up rows only.
"""

import base64
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.imports import get_categorizer
from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.bank.plaid import SyncPage
from app.bank.token_store import SqliteTokenStore
from app.config import get_settings
from app.imports.categorizer import CategorizeError, MerchantCategory, MerchantToCategorize
from app.imports.parser import (
    ColumnMapping,
    FileTooLargeError,
    ImportFileError,
    parse_amount,
    parse_date,
    parse_statement,
)
from app.imports.rules import keyword_kind
from app.main import app
from app.services import bank as bank_service
from app.services import imports as service
from app.services.bank_categories import set_category
from app.services.imports import merge_plaid_with_imports
from app.services.usage import UsageLimiter
from app.sheets.setup import ensure_spreadsheet
from app.storage.dependencies import get_repository
from app.storage.models import BankAccount, BankTransaction, BankTransactionFilter, Category, ReceiptDraft
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_auth import make_settings
from tests.test_bank import FakeProvider, plaid_tx

NOW = "2026-10-22T12:00:00Z"
USER = "google-sub-1"

# CIBC: no header; date, description, debit, credit, and on credit cards the masked card number.
CIBC_CARD = (
    b"2026-10-01,TIM HORTONS #1234 VANCOUVER BC,4.50,,4500********1234\n"
    b"2026-10-02,PAYMENT THANK YOU/PAIEMENT MERCI,,250.00,4500********1234\n"
    b"2026-10-03,SAFEWAY #4501 BURNABY BC,87.12,,4500********1234\n"
    b"2026-10-03,SAFEWAY #4501 BURNABY BC,87.12,,4500********1234\n"
    b"2026-10-04,AMAZON.CA*2G9HF48P3 AMAZON.CA ON,,19.99,4500********1234\n"
)
CIBC_BANK = (
    b"2026-10-01,Point of Sale - Interac RETAIL PURCHASE 000001234567 SUZUYA JAPANESE,23.10,\n"
    b"2026-10-05,Electronic Funds Transfer PAY ACME CORP,,2500.00\n"
    b"2026-10-06,Internet Banking INTERNET BILL PAY 000000123456 CIBC VISA,300.00,\n"
)
TD = (
    b"10/14/2026,STARBUCKS,5.25,,1200.00\n"
    b"10/15/2026,PAYROLL,,1500.00,2700.00\n"
    b"10/16/2026,COSTCO,120.33,,2579.67\n"
)
RBC = (
    b'"Account Type","Account Number","Transaction Date","Cheque Number","Description 1","Description 2",'
    b'"CAD$","USD$"\n'
    b'Visa,4500123412341234,10/5/2026,,"AMAZON.CA*2G9","",-34.99,\n'
    b'Visa,4500123412341234,10/25/2026,,"PAYMENT - THANK YOU","",500.00,\n'
    b'Visa,4500123412341234,10/26/2026,,"SHELL","",-60.00,\n'
)
BMO = (
    b"Following data is valid as of 20261020123456 (Year/Month/Day/Hour/Minute/Second)\n\n"
    b"Item #,Card #,Transaction Date,Posting Date,Transaction Amount,Description\n"
    b"1,'5191********1234',20261001,20261002,12.50,COFFEE SHOP\n"
    b"2,'5191********1234',20261003,20261004,-40.00,REFUND STORE\n"
    b"3,'5191********1234',20261003,20261004,40.00,STORE\n"
)
TANGERINE_FR = (
    "Date de l'opération;Transaction;Nom;Description;Montant\n"
    "05/10/2026;DEBIT;MÉTRO;Achat;-45,67\n"
    "17/10/2026;DEBIT;PHARMAPRIX;Achat;-12,00\n"
).encode("cp1252")
OFX_SGML = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>CAD
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261006120000.000[-5:EST]
<DTUSER>20261005
<TRNAMT>-12.34
<FITID>9001
<NAME>COFFEE &amp; CO
<MEMO>VISA
</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261006<TRNAMT>100.00<FITID>9002<NAME>REFUND</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""
OFX_XML = b"""<?xml version="1.0"?><?OFX OFXHEADER="200" VERSION="220"?>
<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CURDEF>USD</CURDEF><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261007</DTPOSTED><TRNAMT>-9.99</TRNAMT>
<FITID>A1</FITID><NAME>NETFLIX.COM</NAME></STMTTRN>
</BANKTRANLIST></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>"""

CATEGORIES = [
    Category(name="Groceries", icon="", subcategories=["Drinks"]),
    Category(name="Dining Out", icon="", subcategories=["Restaurants", "Coffee & Cafés"]),
    Category(name="Transportation", icon="", subcategories=["Fuel"]),
    Category(name="Other", icon=""),
]


def amounts(data: bytes, account_type: str = "credit") -> list[tuple[date | None, Decimal | None, str]]:
    return [(r.date, r.amount, r.description) for r in parse_statement(data, account_type).rows]


# --- parsing ---------------------------------------------------------------------------------------


def test_cibc_card_has_no_header_and_separate_debit_and_credit_columns() -> None:
    parsed = parse_statement(CIBC_CARD, "credit")
    assert parsed.mapping is not None
    assert (parsed.mapping.date, parsed.mapping.debit, parsed.mapping.credit) == (0, 2, 3)
    assert parsed.mapping.description == [1]  # not the card number column
    assert parsed.mapping_confident
    assert amounts(CIBC_CARD)[:2] == [
        (date(2026, 10, 1), Decimal("4.50"), "TIM HORTONS #1234 VANCOUVER BC"),
        (date(2026, 10, 2), Decimal("-250.00"), "PAYMENT THANK YOU/PAIEMENT MERCI"),
    ]
    assert parsed.columns == ["Column 1", "Column 2", "Column 3", "Column 4", "Column 5"]
    assert parsed.examples[4] == "4500********1234"


def test_cibc_bank_account() -> None:
    assert [a for _, a, _ in amounts(CIBC_BANK, "depository")] == [
        Decimal("23.10"),
        Decimal("-2500.00"),
        Decimal("300.00"),
    ]


def test_td_balance_column_is_ignored() -> None:
    parsed = parse_statement(TD, "depository")
    assert parsed.mapping is not None
    assert (parsed.mapping.date_order, parsed.mapping.debit, parsed.mapping.credit) == ("mdy", 2, 3)
    assert [a for _, a, _ in amounts(TD, "depository")] == [
        Decimal("5.25"),
        Decimal("-1500.00"),
        Decimal("120.33"),
    ]


def test_rbc_signed_amount_per_currency_and_two_description_columns() -> None:
    parsed = parse_statement(RBC, "credit")
    assert parsed.mapping is not None
    assert (parsed.mapping.amount, parsed.mapping.description, parsed.mapping.purchases) == (
        6,
        [4, 5],
        "negative",
    )
    assert amounts(RBC) == [
        (date(2026, 10, 5), Decimal("34.99"), "AMAZON.CA*2G9"),
        (date(2026, 10, 25), Decimal("-500.00"), "PAYMENT - THANK YOU"),
        (date(2026, 10, 26), Decimal("60.00"), "SHELL"),
    ]
    assert [r.line for r in parsed.rows] == [2, 3, 4]


def test_bmo_text_before_the_table_and_purchases_shown_positive() -> None:
    parsed = parse_statement(BMO, "credit")
    assert parsed.mapping is not None
    assert (parsed.mapping.date, parsed.mapping.purchases) == (2, "positive")  # transaction, not posting date
    assert [a for _, a, _ in amounts(BMO)] == [Decimal("12.50"), Decimal("-40.00"), Decimal("40.00")]
    assert [r.line for r in parsed.rows] == [4, 5, 6]


def test_french_headers_semicolons_decimal_comma_and_windows_1252() -> None:
    parsed = parse_statement(TANGERINE_FR, "depository")
    assert parsed.mapping is not None
    assert parsed.mapping.date_order == "dmy"  # 17/10 can only be day first
    assert amounts(TANGERINE_FR, "depository") == [
        (date(2026, 10, 5), Decimal("45.67"), "MÉTRO Achat"),
        (date(2026, 10, 17), Decimal("12.00"), "PHARMAPRIX Achat"),
    ]


def test_day_and_month_order_is_unsure_when_no_value_tells() -> None:
    parsed = parse_statement(b"10/05/2026,COFFEE,4.50,\n11/06/2026,TEA,3.00,\n", "credit")
    assert parsed.mapping is not None
    assert (parsed.mapping.date_order, parsed.mapping_confident) == ("mdy", False)


def test_ofx_sgml_and_xml() -> None:
    sgml = parse_statement(OFX_SGML, "credit")
    assert (sgml.format, sgml.currency, sgml.mapping) == ("ofx", "CAD", None)
    assert [(r.date, r.amount, r.description, r.fitid) for r in sgml.rows] == [
        (date(2026, 10, 5), Decimal("12.34"), "COFFEE & CO VISA", "9001"),  # DTUSER: the purchase date
        (date(2026, 10, 6), Decimal("-100.00"), "REFUND", "9002"),
    ]
    xml = parse_statement(OFX_XML, "credit")
    assert (xml.currency, xml.rows[0].amount, xml.rows[0].fitid) == ("USD", Decimal("9.99"), "A1")


def test_rows_without_a_date_or_amount_are_skipped_with_a_reason() -> None:
    good = b"".join(b"2026-10-%02d,COFFEE,4.50,\n" % day for day in range(1, 21))
    rows = parse_statement(good + b"soon,TEA,3.00,\n2026-10-22,NOTHING,,\n", "credit").rows
    assert [(r.line, r.problem) for r in rows[-3:]] == [
        (20, ""),
        (21, "Unreadable date “soon”"),
        (22, "No amount"),
    ]


def test_saved_mapping_is_used_for_the_same_layout_and_a_chosen_one_wins() -> None:
    detected = parse_statement(TD, "depository").mapping
    assert detected is not None
    flipped = detected.model_copy(update={"debit": 3, "credit": 2})
    assert parse_statement(TD, "depository", saved=flipped).rows[0].amount == Decimal("-5.25")
    other_layout = flipped.model_copy(update={"signature": "columns:9"})
    assert parse_statement(TD, "depository", saved=other_layout).rows[0].amount == Decimal("5.25")
    chosen = ColumnMapping(date=0, date_order="mdy", description=[1], amount=4, purchases="positive")
    assert parse_statement(TD, "depository", saved=detected, chosen=chosen).rows[0].amount == Decimal(
        "1200.00"
    )


def test_files_that_are_not_statements_are_refused() -> None:
    with pytest.raises(ImportFileError):
        parse_statement(b"hello\nworld\n", "credit")
    with pytest.raises(ImportFileError):
        parse_statement(b"name,city\nAnn,Ottawa\nBob,Laval\n", "credit")
    with pytest.raises(FileTooLargeError):
        parse_statement(b"x" * (2 * 1024 * 1024 + 1), "credit")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.56", Decimal("1234.56")),
        ("(12.34)", Decimal("-12.34")),
        ("12.34-", Decimal("-12.34")),
        ("12.34 CR", Decimal("-12.34")),
        ("12.34 DR", Decimal("12.34")),
        ("1 234,56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),
        ("1,234", Decimal("1234")),
        ("-5", Decimal("-5")),
        ("", None),
        ("abc", None),
        ("2026-10-05", None),
        ("4500****1234", None),
    ],
)
def test_amounts(text: str, expected: Decimal | None) -> None:
    assert parse_amount(text) == expected


def test_dates() -> None:
    assert parse_date("2026-10-05T00:00:00", "ymd") == date(2026, 10, 5)
    assert parse_date("Oct 5, 2026", "mdy") == date(2026, 10, 5)
    assert parse_date("05-oct-2026", "mdy") == date(2026, 10, 5)
    assert parse_date("5 févr. 2026", "dmy") == date(2026, 2, 5)
    assert parse_date("5/10/2026", "dmy") == date(2026, 10, 5)
    assert parse_date("5/10/2026", "mdy") == date(2026, 5, 10)
    assert parse_date("13/13/2026", "mdy") is None


@pytest.mark.parametrize(
    ("text", "amount", "account_type", "expected"),
    [
        ("PAYMENT THANK YOU/PAIEMENT MERCI", "-250", "credit", "transfer"),
        ("SAFEWAY #4501", "-20", "credit", None),  # a refund
        ("Internet Banking INTERNET BILL PAY 000000 CIBC VISA", "300", "depository", "transfer"),
        ("Internet Banking E-TRANSFER 1234 Jane", "100", "depository", "transfer"),
        ("Electronic Funds Transfer PAY ACME CORP", "-2500", "depository", "income"),
        ("VIREMENT INTERAC", "-50", "depository", "transfer"),
        ("BC HYDRO BILL PAY", "80", "depository", None),
        ("SAFEWAY", "20", "depository", None),
    ],
)
def test_keyword_rules(text: str, amount: str, account_type: str, expected: str | None) -> None:
    assert keyword_kind(text, Decimal(amount), account_type) == expected


# --- importing -------------------------------------------------------------------------------------


class FakeCategorizer:
    def __init__(self, answers: dict[str, tuple[str, str, str | None]] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[list[MerchantToCategorize]] = []
        self.fail = False

    def categorize(
        self, merchants: list[MerchantToCategorize], categories: list[Category]
    ) -> list[MerchantCategory]:
        self.asked.append(merchants)
        if self.fail:
            raise CategorizeError("down")
        return [
            MerchantCategory(id=m.id, kind=kind, category=category, subcategory=sub)  # type: ignore[arg-type]
            for m in merchants
            if m.text in self.answers
            for kind, category, sub in [self.answers[m.text]]
        ]


@pytest.fixture
def repository() -> SheetsRepository:
    api = FakeSheetsApi()
    return SheetsRepository(api, ensure_spreadsheet(api, NOW), TabReadCache(ttl_seconds=0))


def card(repository: SheetsRepository, account_type: str = "credit") -> BankAccount:
    draft = service.AccountDraft(
        institution="CIBC", name="Aventura Visa", type=account_type, mask="1234", currency="CAD"
    )  # type: ignore[arg-type]
    return service.create_account(repository, draft)


def do_import(
    repository: SheetsRepository,
    account: BankAccount,
    data: bytes,
    categorizer: FakeCategorizer | None = None,
    include: set[int] | None = None,
) -> service.ImportResult:
    return service.run_import(
        repository, categorizer, USER, account.account_id, "cibc.csv", data, None, include or set(), NOW
    )


def active(repository: SheetsRepository) -> list[BankTransaction]:
    return sorted(
        repository.list_bank_transactions(BankTransactionFilter()), key=lambda t: (t.date, t.amount)
    )


def test_import_saves_rows_once_and_reimporting_finds_duplicates(repository: SheetsRepository) -> None:
    account = card(repository)
    first = do_import(repository, account, CIBC_CARD)
    assert (first.added, first.duplicates, first.skipped) == (5, 0, 0)
    safeway = [t for t in active(repository) if "SAFEWAY" in t.description]
    assert len(safeway) == 2  # two identical charges on one day stay two
    assert safeway[0].import_key != safeway[1].import_key
    assert {t.merchant for t in safeway} == {"Safeway Burnaby BC"}
    assert all(t.currency == "CAD" and t.account_id == account.account_id for t in safeway)

    overlapping = CIBC_CARD + b"2026-10-08,SHELL C12345 BURNABY BC,60.00,,4500********1234\n"
    second = do_import(repository, account, overlapping)
    assert (second.added, second.duplicates) == (1, 5)
    assert len(active(repository)) == 6
    # The confirmed mapping is saved on the account.
    saved = next(a for a in repository.list_bank_accounts() if a.account_id == account.account_id)
    assert ColumnMapping.model_validate_json(saved.import_format).debit == 2


def test_categories_come_from_rules_history_keywords_then_claude(repository: SheetsRepository) -> None:
    account = card(repository)
    categorizer = FakeCategorizer(
        {
            "Tim Hortons Vancouver BC": ("spending", "dining out", "coffee & cafés"),  # sheet spelling wins
            "Safeway Burnaby BC": ("spending", "Groceries", "Not a subcategory"),
            "Amazon.Ca Amazon.Ca ON": ("spending", "Made Up", None),
        }
    )
    do_import(repository, account, CIBC_CARD, categorizer)
    choices = {t.merchant: (t.kind, t.category, t.subcategory) for t in active(repository)}
    assert choices == {
        "Tim Hortons Vancouver BC": ("spending", "Dining Out", "Coffee & Cafés"),
        "Thank You/Paiement Merci": ("transfer", "", ""),
        "Safeway Burnaby BC": ("spending", "Groceries", ""),
        "Amazon.Ca Amazon.Ca ON": ("spending", "Other", ""),
    }
    # Only merchants without a rule were asked about, once each, without amounts or dates.
    [asked] = categorizer.asked
    assert sorted(m.text for m in asked) == [
        "Amazon.Ca Amazon.Ca ON",
        "Safeway Burnaby BC",
        "Tim Hortons Vancouver BC",
    ]
    assert {m.money for m in asked if m.text.startswith("Amazon")} == {"in"}

    # A later file: known merchants keep their category without asking Claude again.
    do_import(
        repository, account, b"2026-10-20,TIM HORTONS #999 VANCOUVER BC,3.10,,4500********1234\n", categorizer
    )
    assert len(categorizer.asked) == 1
    assert active(repository)[-1].category == "Dining Out"


def test_import_succeeds_without_claude(repository: SheetsRepository) -> None:
    categorizer = FakeCategorizer()
    categorizer.fail = True
    do_import(repository, card(repository, "depository"), CIBC_BANK, categorizer)
    assert [(t.kind, t.category) for t in active(repository)] == [
        ("spending", "Other"),
        ("income", ""),  # ACME payroll (keyword rule)
        ("transfer", ""),  # paying the Visa from the bank account
    ]


def test_possible_duplicates_need_the_users_ok(repository: SheetsRepository) -> None:
    account = card(repository)
    plaid_row = BankTransaction(
        transaction_id="plaid-1",
        account_id=account.account_id,
        date=date(2026, 10, 2),
        merchant="Tim Hortons",
        description="TIM HORTONS 1234",
        amount=Decimal("4.50"),
        currency="CAD",
        kind="spending",
        category="Dining Out",
        subcategory="",
        plaid_category="",
        pending=False,
    )
    repository.apply_bank_changes([plaid_row], [])
    data = b"2026-10-01,TIM HORTONS #1234 VANCOUVER BC,4.50,,\n"
    preview = service.preview_import(repository, account.account_id, data, None)
    assert preview.counts.possible_duplicate == 1
    assert preview.rows[0].reason == "Looks like Tim Hortons on 2026-10-02"

    assert do_import(repository, account, data).added == 0
    assert do_import(repository, account, data, include={1}).added == 1
    assert len(active(repository)) == 2


def test_undo_removes_the_imports_rows_and_allows_importing_again(repository: SheetsRepository) -> None:
    account = card(repository)
    result = do_import(repository, account, CIBC_CARD)
    [record] = repository.list_imports()
    assert (record.added, record.date_from, record.date_to, record.filename) == (
        5,
        date(2026, 10, 1),
        date(2026, 10, 4),
        "cibc.csv",
    )
    assert service.undo_import(repository, USER, result.import_id) == 5
    assert active(repository) == [] and repository.list_imports() == []
    with pytest.raises(service.ImportNotFoundError):
        service.undo_import(repository, USER, result.import_id)
    assert do_import(repository, account, CIBC_CARD).added == 5


def test_imported_charge_matches_a_receipt(repository: SheetsRepository) -> None:
    receipt = repository.create_receipt(
        ReceiptDraft.model_validate(
            {
                "store": "Safeway",
                "purchased_at": "2026-10-03",
                "total": "87.12",
                "items": [{"name": "Groceries", "price": "87.12", "category": "Groceries"}],
            }
        )
    )
    do_import(repository, card(repository), CIBC_CARD)
    matched = [t for t in active(repository) if t.receipt_id == receipt.receipt_id]
    assert len(matched) == 1


def test_ofx_currency_must_match_and_linked_accounts_are_refused(repository: SheetsRepository) -> None:
    account = card(repository)
    with pytest.raises(ImportFileError, match="USD"):
        service.preview_import(repository, account.account_id, OFX_XML, None)
    assert do_import(repository, account, OFX_SGML).added == 2
    assert {t.import_key for t in active(repository)} == {"ofx:9001", "ofx:9002"}

    linked = BankAccount(
        account_id="plaid-acc", item_id="item-1", institution="Bank", name="Card", mask="", type="credit",
        subtype="", currency="CAD",
    )  # fmt: skip
    repository.save_bank_accounts([linked])
    with pytest.raises(service.LinkedAccountError):
        service.preview_import(repository, "plaid-acc", CIBC_CARD, None)
    with pytest.raises(service.LinkedAccountError):
        service.delete_account(repository, "plaid-acc", remove_transactions=False)


# --- API -------------------------------------------------------------------------------------------


@pytest.fixture
def http(repository: SheetsRepository, signed_in: UsageLimiter) -> Iterator[TestClient]:
    user = SessionData(sub=USER, email="t@example.com", refresh_token="r", spreadsheet_id="s")
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_categorizer] = lambda: FakeCategorizer()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def file_body(account_id: str, data: bytes, **extra: Any) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "filename": "statement.csv",
        "data": base64.b64encode(data).decode(),
        **extra,
    }


def test_api_flow(http: TestClient) -> None:
    created = http.post(
        "/api/accounts",
        json={"institution": "CIBC", "name": "Visa", "type": "credit", "mask": "1234", "currency": "cad"},
    )
    assert created.status_code == 201
    account_id = created.json()["account_id"]
    assert created.json()["currency"] == "CAD"

    preview = http.post("/api/imports/preview", json=file_body(account_id, CIBC_CARD)).json()
    assert preview["counts"] == {"new": 5, "duplicate": 0, "possible_duplicate": 0, "skipped": 0}
    assert (preview["date_from"], preview["date_to"]) == ("2026-10-01", "2026-10-04")
    assert preview["rows"][1]["amount"] == "-250.00"
    assert http.get("/api/bank/transactions").json() == []  # a preview saves nothing

    done = http.post("/api/imports", json=file_body(account_id, CIBC_CARD))
    assert done.status_code == 201
    assert done.json()["added"] == 5
    [info] = http.get("/api/accounts").json()
    assert (info["linked"], info["last_import"]["added"]) == (False, 5)
    [record] = http.get("/api/imports", params={"account_id": account_id}).json()

    assert http.delete(f"/api/imports/{record['import_id']}").status_code == 204
    assert http.get("/api/bank/transactions").json() == []
    renamed = http.put(
        f"/api/accounts/{account_id}",
        json={"institution": "CIBC", "name": "Aventura", "type": "credit", "currency": "CAD"},
    )
    assert renamed.json()["name"] == "Aventura"
    assert http.delete(f"/api/accounts/{account_id}").status_code == 204
    assert http.get("/api/accounts").json() == []


def test_api_errors(http: TestClient) -> None:
    account_id = http.post(
        "/api/accounts", json={"institution": "CIBC", "name": "Visa", "type": "credit", "currency": "CAD"}
    ).json()["account_id"]
    assert http.post("/api/imports/preview", json=file_body("nope", CIBC_CARD)).status_code == 404
    bad = http.post("/api/imports/preview", json=file_body(account_id, b"hello\nworld\n"))
    assert bad.status_code == 422 and "statement" in bad.json()["detail"]
    not_base64 = http.post("/api/imports/preview", json={"account_id": account_id, "data": "%%%"})
    assert not_base64.status_code == 422
    huge = http.post("/api/imports/preview", json={"account_id": account_id, "data": "A" * 3_000_000})
    assert huge.status_code == 413
    no_amount = {"date": 0, "description": [1]}
    assert (
        http.post(
            "/api/imports/preview", json=file_body(account_id, CIBC_CARD, mapping=no_amount)
        ).status_code
        == 422
    )
    assert http.delete("/api/imports/missing").status_code == 404
    assert (
        http.post(
            "/api/accounts", json={"institution": "", "name": "x", "type": "credit", "currency": "CAD"}
        ).status_code
        == 422
    )


def test_charges_plaid_imported_for_the_same_card_are_not_imported_again(
    repository: SheetsRepository,
) -> None:
    plaid_card = BankAccount(
        account_id="plaid-acc", item_id="item-1", institution="CIBC", name="CIBC Aventura Visa", mask="1234",
        type="credit", subtype="credit card", currency="CAD",
    )  # fmt: skip
    other_card = plaid_card.model_copy(update={"account_id": "plaid-other", "mask": "9999"})
    repository.save_bank_accounts([plaid_card, other_card])

    def plaid_row(transaction_id: str, account_id: str, day: int, text: str, amount: str) -> BankTransaction:
        return BankTransaction(
            transaction_id=transaction_id, account_id=account_id, date=date(2026, 10, day), merchant="",
            description=text, amount=Decimal(amount), currency="CAD", kind="spending", category="Other",
            subcategory="", plaid_category="", pending=False,
        )  # fmt: skip

    repository.apply_bank_changes(
        [
            plaid_row(
                "p1", "plaid-acc", 2, "TIM HORTONS #1234 VANCOUVER BC", "4.50"
            ),  # purchase date a day off
            plaid_row(
                "p2", "plaid-acc", 3, "SAFEWAY #4501 BURNABY BC", "87.12"
            ),  # one of the two Safeway charges
            plaid_row(
                "p3", "plaid-other", 4, "AMAZON.CA*2G9HF48P3 AMAZON.CA ON", "-19.99"
            ),  # a different card
        ],
        [],
    )
    account = card(repository)  # CIBC, last 4 digits 1234
    preview = service.preview_import(repository, account.account_id, CIBC_CARD, None)
    assert [(r.status, r.reason) for r in preview.rows] == [
        (
            "possible_duplicate",
            "Looks like TIM HORTONS #1234 VANCOUVER BC on 2026-10-02"
            " in CIBC Aventura Visa •• 1234 (from Plaid)",
        ),
        ("new", ""),
        (
            "possible_duplicate",
            "Looks like SAFEWAY #4501 BURNABY BC on 2026-10-03 in CIBC Aventura Visa •• 1234 (from Plaid)",
        ),
        ("new", ""),
        ("new", ""),
    ]
    assert do_import(repository, account, CIBC_CARD).added == 3

    # Without last 4 digits, the same bank and account type count as the same card.
    unlabelled = service.create_account(
        repository,
        service.AccountDraft(institution="cibc", name="Visa", type="credit", currency="CAD"),
    )
    again = service.preview_import(repository, unlabelled.account_id, CIBC_CARD, None)
    assert again.counts.new == 0  # every row is in one of the CIBC cards already


def test_plaid_charges_replace_imported_statement_rows(repository: SheetsRepository) -> None:
    """A statement imported while Plaid was behind, then Plaid catches up (phase 12)."""
    plaid_card = BankAccount(
        account_id="plaid-acc", item_id="item-1", institution="CIBC", name="CIBC Aventura Visa", mask="1234",
        type="credit", subtype="credit card", currency="CAD",
    )  # fmt: skip
    repository.save_bank_accounts([plaid_card])
    account = card(repository)
    do_import(repository, account, CIBC_CARD)
    imported = {t.description: t for t in active(repository)}
    tim = imported["TIM HORTONS #1234 VANCOUVER BC"]
    set_category(
        repository, tim.transaction_id, "spending", "Dining Out", "Coffee & Cafés", apply_to_merchant=False
    )
    repository.set_bank_receipt_ids({tim.transaction_id: "receipt-1"})

    def plaid_row(
        transaction_id: str, day: int, text: str, amount: str, pending: bool = False
    ) -> BankTransaction:
        return BankTransaction(
            transaction_id=transaction_id, account_id="plaid-acc", date=date(2026, 10, day), merchant="",
            description=text, amount=Decimal(amount), currency="CAD", kind="spending", category="Other",
            subcategory="", plaid_category="", pending=pending,
        )  # fmt: skip

    repository.apply_bank_changes(
        [
            plaid_row("p1", 2, "TIM HORTONS 1234", "4.50"),
            plaid_row("p2", 3, "SAFEWAY #4501 BURNABY BC", "87.12"),  # one of the two Safeway charges
            plaid_row("p3", 4, "AMAZON.CA*2G9HF48P3", "-19.99", pending=True),  # pending: left alone
            plaid_row("p4", 9, "SHELL C12345", "60.00"),  # not in the statement
        ],
        [],
    )
    assert merge_plaid_with_imports(repository, {"p1", "p2"}) == 2
    left = {t.transaction_id: t for t in active(repository)}
    assert {"p1", "p2", "p3", "p4"} <= set(left)
    assert len(left) == 4 + 3  # payment, the second Safeway, and the Amazon refund stay imported
    # The user's choice and the receipt match moved to the Plaid copy.
    assert (left["p1"].category, left["p1"].subcategory, left["p1"].edited) == (
        "Dining Out",
        "Coffee & Cafés",
        True,
    )
    assert left["p1"].receipt_id == "receipt-1"
    assert (left["p2"].category, left["p2"].edited) == ("Other", False)  # nothing chosen: Plaid's own
    assert tim.transaction_id not in left

    # Only the rows a sync just added are paired; None checks them all (the one-time run).
    assert merge_plaid_with_imports(repository, {"p4"}) == 0
    assert merge_plaid_with_imports(repository, None) == 0


def test_plaid_charge_replaces_a_statement_row_posted_days_later(repository: SheetsRepository) -> None:
    """CIBC's file has the posting date: a hotel booked on Oct 9 is in it on Oct 13."""
    plaid_card = BankAccount(
        account_id="plaid-acc", item_id="item-1", institution="CIBC", name="CIBC Aventura Visa", mask="1234",
        type="credit", subtype="credit card", currency="CAD",
    )  # fmt: skip
    repository.save_bank_accounts([plaid_card])
    account = card(repository)
    do_import(
        repository,
        account,
        b"2026-10-13,BKG*BOOKING.COM HOTEL (888)850-3958,374.68,,4500********1234\n"
        b"2026-10-14,TIM HORTONS #1234 VANCOUVER BC,4.50,,4500********1234\n"
        b"2026-10-25,BKG*BOOKING.COM HOTEL (888)850-3958,374.68,,4500********1234\n",
    )
    repository.apply_bank_changes(
        [
            BankTransaction(
                transaction_id="p1", account_id="plaid-acc", date=date(2026, 10, 9), merchant="Booking.com",
                description="BKG*BOOKING.COM HOTEL (888)", amount=Decimal("374.68"), currency="CAD",
                kind="spending", category="Travel", subcategory="", plaid_category="", pending=False,
            )
        ],
        [],
    )  # fmt: skip

    assert merge_plaid_with_imports(repository, None) == 1
    # The Oct 13 row went; the second booking, 16 days later, is another charge.
    assert [(t.date.day, t.amount, t.import_id == "") for t in active(repository)] == [
        (9, Decimal("374.68"), True),
        (14, Decimal("4.50"), False),
        (25, Decimal("374.68"), False),
    ]


def test_sync_merges_new_plaid_charges_with_imported_rows(
    repository: SheetsRepository, tmp_path: Path
) -> None:
    store = SqliteTokenStore(tmp_path / "tokens.sqlite3", os.urandom(32))
    provider = FakeProvider()  # links "acc-1", a "Plaid Credit Card" •• 3333 at First Platypus Bank
    provider.pages[""] = [SyncPage(next_cursor="c1", has_more=False)]
    bank_service.link_item(provider, store, repository, USER, "public-token", CATEGORIES, "CAD", NOW)
    statement = service.create_account(
        repository,
        service.AccountDraft(
            institution="First Platypus Bank", name="Card", type="credit", mask="3333", currency="CAD"
        ),
    )
    do_import(repository, statement, b"2026-10-05,FRESHCO #123,12.50,\n")
    assert len(active(repository)) == 1

    provider.pages["c1"] = [
        SyncPage(added=[plaid_tx("t1", "12.50", day=5)], next_cursor="c2", has_more=False)
    ]
    bank_service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)
    assert [t.transaction_id for t in active(repository)] == ["t1"]


def test_import_daily_limit(http: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(daily_import_limit=1)
    account_id = http.post(
        "/api/accounts", json={"institution": "CIBC", "name": "Visa", "type": "credit", "currency": "CAD"}
    ).json()["account_id"]
    body = file_body(account_id, CIBC_CARD)
    for _ in range(3):  # previews don't call Claude, so they aren't counted
        assert http.post("/api/imports/preview", json=body).status_code == 200
    assert http.post("/api/imports", json=body).status_code == 201

    refused = http.post("/api/imports", json=body)
    assert refused.status_code == 429
    assert refused.json()["detail"].startswith("You've used all 1 statement imports for today.")

    # Without an API key nothing is categorized by Claude, so nothing is limited.
    app.dependency_overrides[get_categorizer] = lambda: None
    assert http.post("/api/imports", json=body).status_code == 201
