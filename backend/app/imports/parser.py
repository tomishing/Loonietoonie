"""Read a statement file downloaded from a bank: CSV (any column layout) or OFX/QFX.

CSV files differ per bank (CIBC has no header row and separate debit and credit columns, RBC
has a signed amount per currency, BMO starts with a line of text), so the columns are detected:
by header name first, then by content. The result is a `ColumnMapping` the user confirms in the
preview; it is saved on the account and used first next time.

Amounts come out with Plaid's sign: positive = money spent, negative = money in.
"""

import csv
import io
import re
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5_000
# A column is a date or amount column when at least this share of its filled cells parse as one.
DETECT_SHARE = 0.9
# Debit and credit columns are rarely both filled on one row.
MAX_DEBIT_CREDIT_OVERLAP = 0.05

DateOrder = Literal["ymd", "mdy", "dmy"]
FileFormat = Literal["csv", "ofx"]


class ImportFileError(Exception):
    """The file can't be read as a statement (the message is shown to the user)."""


class FileTooLargeError(ImportFileError):
    pass


class ColumnMapping(BaseModel):
    """Which CSV columns hold what (0-based indexes). Either `amount`, or `debit` and/or `credit`."""

    date: int = Field(ge=0)
    date_order: DateOrder = "ymd"
    description: list[int] = Field(min_length=1, max_length=10)
    amount: int | None = Field(default=None, ge=0)
    debit: int | None = Field(default=None, ge=0)
    credit: int | None = Field(default=None, ge=0)
    # For a signed `amount`: how the file shows purchases (money out).
    purchases: Literal["negative", "positive"] = "negative"
    # The file's layout this mapping was made for: its header names, or its column count.
    signature: str = ""

    @model_validator(mode="after")
    def _has_amount(self) -> "ColumnMapping":
        if self.amount is None and self.debit is None and self.credit is None:
            raise ValueError("Choose an amount column, or debit and credit columns")
        return self

    def columns(self) -> list[int]:
        return [
            i for i in (self.date, *self.description, self.amount, self.debit, self.credit) if i is not None
        ]


class ParsedRow(BaseModel):
    line: int  # 1-based line number in the file (record number for OFX)
    date: date | None
    description: str
    amount: Decimal | None
    # OFX FITID: the bank's own transaction ID.
    fitid: str = ""
    # Why the row can't be imported, or "" when it can.
    problem: str = ""


class ParsedFile(BaseModel):
    format: FileFormat
    # Column names for the mapping editor (the header, or "Column 1", "Column 2", …), and an
    # example value per column. Empty for OFX.
    columns: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    mapping: ColumnMapping | None = None
    # False when the columns or the day/month order were guessed; the app then asks to check them.
    mapping_confident: bool = True
    currency: str = ""  # OFX CURDEF
    rows: list[ParsedRow]


def parse_statement(
    data: bytes, account_type: str, saved: ColumnMapping | None = None, chosen: ColumnMapping | None = None
) -> ParsedFile:
    """Parse a statement file. `chosen` is a mapping the user just set in the preview; `saved` is the
    account's mapping from an earlier import, used when the file has the same layout."""
    if len(data) > MAX_FILE_BYTES:
        raise FileTooLargeError("The file is larger than 2 MB. Download a shorter date range.")
    text = decode(data)
    if _looks_like_ofx(text):
        return parse_ofx(text)
    return parse_csv(text, account_type, saved, chosen)


def decode(data: bytes) -> str:
    """UTF-8 (with or without a byte-order mark), else Windows-1252 (older French exports)."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# --- CSV ----------------------------------------------------------------------------------------


def parse_csv(
    text: str, account_type: str, saved: ColumnMapping | None = None, chosen: ColumnMapping | None = None
) -> ParsedFile:
    numbered, header = _read_table(text)
    table = [row for _, row in numbered]
    width = max(len(row) for row in table) if table else 0
    if not table or width < 2:
        raise ImportFileError("No transactions found. Is this a CSV statement from your bank?")
    if len(table) > MAX_ROWS:
        raise FileTooLargeError(f"The file has more than {MAX_ROWS:,} rows. Download a shorter date range.")
    names = [cell.strip() for cell in header] + [""] * (width - len(header)) if header else [""] * width
    columns = [name or f"Column {i + 1}" for i, name in enumerate(names)]
    signature = layout_signature(header, width)

    if chosen is not None:
        mapping, confident = chosen.model_copy(update={"signature": signature}), True
    elif saved is not None and saved.signature == signature and max(saved.columns()) < width:
        mapping, confident = saved, True
    else:
        mapping, confident = detect_mapping(table, names, account_type)
        mapping = mapping.model_copy(update={"signature": signature})
    if max(mapping.columns()) >= width:
        raise ImportFileError("The chosen columns aren't in this file.")

    rows = [_csv_row(cells, line, mapping) for line, cells in numbered]
    examples = [next((r[i].strip() for r in table if i < len(r) and r[i].strip()), "") for i in range(width)]
    return ParsedFile(
        format="csv",
        columns=columns,
        examples=examples,
        mapping=mapping,
        mapping_confident=confident,
        rows=rows,
    )


def layout_signature(header: list[str] | None, width: int) -> str:
    if header:
        return "header:" + "|".join(_normalize(cell) for cell in header)
    return f"columns:{width}"


def _read_table(text: str) -> tuple[list[tuple[int, list[str]]], list[str] | None]:
    """The transaction table: its rows with their 1-based record numbers, and its header (if any).

    Lines before the table (BMO: "Following data is valid as of …") and blank or one-cell lines are
    skipped: the table starts at the first row with the file's most common cell count.
    """
    sample = text[:20_000]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    lines = list(csv.reader(io.StringIO(text), dialect))
    kept = [
        (number, row)
        for number, row in enumerate(lines, start=1)
        if len(row) >= 2 and any(cell.strip() for cell in row)
    ]
    if not kept:
        return [], None
    common = Counter(len(row) for _, row in kept).most_common(1)[0][0]
    table = kept[next(i for i, (_, row) in enumerate(kept) if len(row) == common) :]
    if _is_header(table[0][1]):
        return table[1:], table[0][1]
    return table, None


def _is_header(row: list[str]) -> bool:
    cells = [cell.strip() for cell in row if cell.strip()]
    if not cells:
        return False
    dates = sum(1 for cell in cells if _date_parts(cell) is not None)
    amounts = sum(1 for cell in cells if parse_amount(cell) is not None)
    return dates == 0 and amounts < 2


# Header names, normalized (lower case, no accents or punctuation). English and French.
_DATE_HEADERS = ("transaction date", "date de transaction", "date de l operation", "date d operation", "date")
_POSTED_HEADERS = (
    "posting date",
    "posted date",
    "date posted",
    "date de comptabilisation",
    "date d inscription",
)
_DESCRIPTION_HEADERS = frozenset(
    {
        "description", "description 1", "description 2", "name", "memo", "payee", "merchant",
        "merchant name", "details", "transaction details", "transaction description", "libelle",
        "description de l operation", "description de la transaction", "detail", "details de l operation",
        "nom", "nom du commercant", "marchand", "commercant", "beneficiaire",
    }
)  # fmt: skip
_AMOUNT_HEADERS = frozenset(
    {"amount", "transaction amount", "montant", "montant de la transaction", "value", "valeur"}
)
_DEBIT_HEADERS = frozenset(
    {
        "debit",
        "debits",
        "withdrawal",
        "withdrawals",
        "money out",
        "paid out",
        "retrait",
        "retraits",
        "debit amount",
    }
)
_CREDIT_HEADERS = frozenset(
    {"credit", "credits", "deposit", "deposits", "money in", "paid in", "depot", "depots", "credit amount"}
)
# RBC: one amount column per currency, "CAD$" and "USD$" (normalized "cad", "usd").
_CURRENCY_HEADERS = frozenset({"cad", "usd", "eur", "gbp", "jpy", "aud", "cny", "krw", "mxn"})


def detect_mapping(table: list[list[str]], names: list[str], account_type: str) -> tuple[ColumnMapping, bool]:
    """Guess the columns: by header name where the header says it, then by content. Returns the
    mapping and whether it is certain."""
    width = len(names)
    normalized = [_normalize(name) for name in names]
    stats = [_column_stats(table, i) for i in range(width)]
    confident = True

    date_column = _pick_date_column(normalized, stats)
    if date_column is None:
        raise ImportFileError("No date column found. Is this a statement from your bank?")
    order, sure = _date_order([row[date_column] for row in table if date_column < len(row)])
    confident = confident and sure

    amount = debit = credit = None
    named_debit = [i for i, n in enumerate(normalized) if n in _DEBIT_HEADERS]
    named_credit = [i for i, n in enumerate(normalized) if n in _CREDIT_HEADERS]
    named_amount = [i for i, n in enumerate(normalized) if n in _AMOUNT_HEADERS]
    currency_columns = [i for i, n in enumerate(normalized) if n in _CURRENCY_HEADERS and stats[i].amounts]
    if named_debit or named_credit:
        debit = named_debit[0] if named_debit else None
        credit = named_credit[0] if named_credit else None
    elif named_amount:
        amount = named_amount[0]
    elif currency_columns:
        # RBC: one column per currency; the fuller one is the account's.
        amount = max(currency_columns, key=lambda i: stats[i].filled)
    else:
        numeric = [i for i in range(width) if i != date_column and stats[i].is_amount]
        pair = _debit_credit_pair(table, numeric)
        if pair is not None:
            debit, credit = pair
        elif numeric:
            # Amount before balance: the first numeric column. Unsure when there are several.
            amount = numeric[0]
            confident = confident and len(numeric) == 1
        else:
            raise ImportFileError("No amount column found. Is this a statement from your bank?")

    used = {date_column, amount, debit, credit}
    described = [i for i, n in enumerate(normalized) if n in _DESCRIPTION_HEADERS and i not in used]
    if not described:
        described = [i for i in range(width) if i not in used and stats[i].is_text]
    if not described:
        raise ImportFileError("No description column found. Is this a statement from your bank?")

    purchases: Literal["negative", "positive"] = "negative"
    if amount is not None:
        purchases = _purchase_sign(table, amount, account_type)
    return (
        ColumnMapping(
            date=date_column,
            date_order=order,
            description=described,
            amount=amount,
            debit=debit,
            credit=credit,
            purchases=purchases,
        ),
        confident,
    )


class _ColumnStats(BaseModel):
    filled: int
    dates: int
    amounts: int
    distinct: int
    # Cells that are mostly digits and stars: card or account numbers (CIBC's last column).
    numbers: int

    @property
    def is_date(self) -> bool:
        return self.filled > 0 and self.dates >= DETECT_SHARE * self.filled

    @property
    def is_amount(self) -> bool:
        return self.filled > 0 and not self.is_date and self.amounts >= DETECT_SHARE * self.filled

    @property
    def is_text(self) -> bool:
        # Text that tells transactions apart: not one repeated value ("Chequing") or card numbers.
        return (
            self.filled > 0
            and not self.is_date
            and not self.is_amount
            and self.numbers < DETECT_SHARE * self.filled
            and (self.distinct > 1 or self.filled == 1)
        )


def _column_stats(table: list[list[str]], index: int) -> _ColumnStats:
    cells = [row[index].strip() for row in table if index < len(row) and row[index].strip()]
    return _ColumnStats(
        filled=len(cells),
        dates=sum(1 for cell in cells if _date_parts(cell) is not None),
        amounts=sum(1 for cell in cells if parse_amount(cell) is not None),
        distinct=len(set(cells)),
        numbers=sum(1 for cell in cells if re.fullmatch(r"[\d*xX#\s-]{6,}", cell)),
    )


def _pick_date_column(normalized: list[str], stats: list[_ColumnStats]) -> int | None:
    # The purchase date ("Transaction Date") before the posting date, by header name.
    for names in (_DATE_HEADERS, _POSTED_HEADERS):
        for name in names:
            for i, header in enumerate(normalized):
                if header == name and stats[i].is_date:
                    return i
    return next((i for i, s in enumerate(stats) if s.is_date), None)


def _debit_credit_pair(table: list[list[str]], numeric: list[int]) -> tuple[int, int] | None:
    """Two neighbouring numeric columns that are rarely both filled: debit, then credit (TD, CIBC).
    A balance column is filled on every row, so it never pairs."""
    for left, right in zip(numeric, numeric[1:], strict=False):
        if right != left + 1:
            continue
        both = either = 0
        for row in table:
            a = left < len(row) and row[left].strip() != ""
            b = right < len(row) and row[right].strip() != ""
            both += a and b
            either += a or b
        if either and both <= MAX_DEBIT_CREDIT_OVERLAP * either and either >= DETECT_SHARE * len(table):
            return left, right
    return None


def _purchase_sign(table: list[list[str]], column: int, account_type: str) -> Literal["negative", "positive"]:
    """How a signed amount column shows purchases. Bank accounts show money out as negative; on a
    credit card most rows are purchases, so the most common sign is theirs."""
    if account_type != "credit":
        return "negative"
    values = [parse_amount(row[column]) for row in table if column < len(row)]
    negative = sum(1 for v in values if v is not None and v < 0)
    positive = sum(1 for v in values if v is not None and v > 0)
    return "negative" if negative > positive else "positive"


def _csv_row(cells: list[str], line: int, mapping: ColumnMapping) -> ParsedRow:
    def cell(index: int | None) -> str:
        return cells[index].strip() if index is not None and index < len(cells) else ""

    description = " ".join(part for part in (cell(i) for i in mapping.description) if part)
    when = parse_date(cell(mapping.date), mapping.date_order)
    amount: Decimal | None
    if mapping.amount is not None:
        value = parse_amount(cell(mapping.amount))
        amount = None if value is None else (-value if mapping.purchases == "negative" else value)
    else:
        debit, credit = parse_amount(cell(mapping.debit)), parse_amount(cell(mapping.credit))
        none = Decimal(0)
        amount = None if debit is None and credit is None else abs(debit or none) - abs(credit or none)
    problem = ""
    if when is None:
        problem = "No date" if not cell(mapping.date) else f"Unreadable date “{cell(mapping.date)}”"
    elif amount is None:
        problem = "No amount"
    elif amount == 0:
        problem = "Amount is 0"
    elif not description:
        problem = "No description"
    return ParsedRow(line=line, date=when, description=description, amount=amount, problem=problem)


# --- dates and amounts ------------------------------------------------------------------------

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "janv", "january", "janvier"),
            ("feb", "fev", "fevr", "february", "fevrier"),
            ("mar", "mars", "march"),
            ("apr", "avr", "april", "avril"),
            ("may", "mai"),
            ("jun", "juin", "june"),
            ("jul", "juil", "july", "juillet"),
            ("aug", "aou", "aout", "august"),
            ("sep", "sept", "september", "septembre"),
            ("oct", "october", "octobre"),
            ("nov", "november", "novembre"),
            ("dec", "december", "decembre"),
        ),
        start=1,
    )
    for name in names
}
_NUMERIC_DATE = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})(?:[ T].*)?$")
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:\d{6})?(?:\.\d+)?(?:\[.*\])?$")
_MONTH_FIRST = re.compile(r"^([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")  # Oct 5, 2026
_DAY_FIRST = re.compile(r"^(\d{1,2})[\s-]+([a-z]+)\.?[\s-]+(\d{4})$")  # 05 Oct 2026, 5-oct-2026


def _date_parts(text: str) -> tuple[str, int, int, int] | None:
    """A date cell's numbers before the day/month order is known: ("ymd", y, m, d) when the order
    is certain from the format, or ("?", a, b, year) for a/b/year."""
    cell = text.strip()
    if match := _COMPACT_DATE.match(cell):
        y, m, d = (int(g) for g in match.groups())
        return ("ymd", y, m, d) if _valid(y, m, d) else None
    if match := _NUMERIC_DATE.match(cell):
        a, b, c = match.groups()
        if len(a) == 4:
            y, m, d = int(a), int(b), int(c)
            return ("ymd", y, m, d) if _valid(y, m, d) else None
        if len(c) == 4 and int(a) <= 31 and int(b) <= 31 and (int(a) <= 12 or int(b) <= 12):
            return ("?", int(a), int(b), int(c))
        return None
    words = _normalize(cell)
    for pattern, month_group in ((_MONTH_FIRST, 1), (_DAY_FIRST, 2)):
        if match := pattern.match(words):
            groups = match.groups()
            month = _MONTHS.get(groups[month_group - 1])
            day = int(groups[1] if month_group == 1 else groups[0])
            year = int(groups[2])
            if month and _valid(year, month, day):
                return ("ymd", year, month, day)
    return None


def _valid(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
    except ValueError:
        return False
    return 1990 <= y <= 2100


def _date_order(cells: list[str]) -> tuple[DateOrder, bool]:
    """The day/month order of a date column, and whether it is certain. A first number above 12
    means day first; a second one means month first. North American banks use month first, so
    that is the guess when no value tells."""
    parts = [p for p in (_date_parts(cell) for cell in cells if cell.strip()) if p is not None]
    ambiguous = [p for p in parts if p[0] == "?"]
    if not ambiguous:
        return "ymd", True
    if any(p[1] > 12 for p in ambiguous):
        return "dmy", True
    if any(p[2] > 12 for p in ambiguous):
        return "mdy", True
    return "mdy", False


def parse_date(text: str, order: DateOrder) -> date | None:
    parts = _date_parts(text)
    if parts is None:
        return None
    kind, a, b, c = parts
    if kind == "ymd":
        return date(a, b, c)
    month, day = (a, b) if order != "dmy" else (b, a)
    return date(c, month, day) if _valid(c, month, day) else None


_AMOUNT = re.compile(r"^(?P<sign>[-+])?\(?(?P<number>\d[\d ,.']*)\)?(?P<trail>-|cr|dr)?$")


def parse_amount(text: str) -> Decimal | None:
    """A money cell as a Decimal, or None when blank or not a number. Accepts "$1,234.56",
    "-12.34", "(12.34)" and "12.34-" (negative), "12.34 CR" (negative) / "DR", and a decimal
    comma ("1 234,56")."""
    cell = text.strip().lower().replace(" ", " ").replace("$", "").replace("cad", "").replace("usd", "")
    cell = re.sub(r"\s+", " ", cell).strip()
    negative = cell.startswith("(") and cell.endswith(")")
    cell = cell.replace(" cr", "cr").replace(" dr", "dr").replace(" -", "-")
    match = _AMOUNT.match(cell.replace("(", "").replace(")", "") if negative else cell)
    if not match:
        return None
    number = match.group("number").strip().replace(" ", "").replace("'", "")
    if re.search(r",\d{2}$", number) and "." not in number[number.rfind(",") :]:
        number = number.replace(".", "").replace(",", ".")  # decimal comma: 1.234,56 or 12,34
    else:
        number = number.replace(",", "")
    if number.count(".") > 1 or not re.fullmatch(r"\d+(?:\.\d+)?", number):
        return None
    try:
        value = Decimal(number)
    except InvalidOperation:
        return None
    if negative or match.group("sign") == "-" or match.group("trail") in ("-", "cr"):
        value = -value
    return value


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.split(r"[^a-z0-9]+", plain)).strip()


# --- OFX / QFX ----------------------------------------------------------------------------------


def _looks_like_ofx(text: str) -> bool:
    head = text[:2_000].upper()
    return "OFXHEADER" in head or "<OFX>" in head or "<?OFX" in head


_TRANSACTION = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.IGNORECASE | re.DOTALL)


def _tag(block: str, name: str) -> str:
    """A leaf value: <NAME>value, closed by </NAME> (XML) or by the next tag or line (SGML)."""
    match = re.search(rf"<{name}>([^<\r\n]*)", block, re.IGNORECASE)
    return _unescape(match.group(1).strip()) if match else ""


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def parse_ofx(text: str) -> ParsedFile:
    """OFX 1.x (SGML, leaf tags not closed) and 2.x (XML). OFX shows money out as negative."""
    blocks = _TRANSACTION.findall(text)
    if not blocks:
        raise ImportFileError("No transactions found in this OFX/QFX file.")
    if len(blocks) > MAX_ROWS:
        raise FileTooLargeError(
            f"The file has more than {MAX_ROWS:,} transactions. Download a shorter date range."
        )
    rows: list[ParsedRow] = []
    for number, block in enumerate(blocks, start=1):
        # DTUSER is the purchase date when the bank sends it; DTPOSTED is when it posted.
        raw_date = _tag(block, "DTUSER") or _tag(block, "DTPOSTED")
        when = parse_date(raw_date[:8], "ymd") if raw_date else None
        value = parse_amount(_tag(block, "TRNAMT"))
        amount = -value if value is not None else None
        name, memo = _tag(block, "NAME"), _tag(block, "MEMO")
        description = name if not memo or memo.casefold() in name.casefold() else f"{name} {memo}".strip()
        problem = ""
        if when is None:
            problem = "No date"
        elif amount is None:
            problem = "No amount"
        elif amount == 0:
            problem = "Amount is 0"
        elif not description:
            problem = "No description"
        rows.append(
            ParsedRow(
                line=number,
                date=when,
                description=description,
                amount=amount,
                fitid=_tag(block, "FITID"),
                problem=problem,
            )
        )
    return ParsedFile(format="ofx", currency=_tag(text, "CURDEF").upper(), rows=rows)
