import base64
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai.receipt_parser import ParsedItem, ParsedReceipt, ReceiptImage, ReceiptParseError
from app.api.scan import get_receipt_parser
from app.config import get_settings
from app.main import app
from app.sheets.schema import FOLDER_MARKER
from app.sheets.setup import ensure_spreadsheet
from app.storage.dependencies import get_repository
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_auth import make_settings

GOOD = ParsedReceipt(
    store="FreshCo",
    purchased_at="2026-10-05",
    currency=None,
    total="5.65",
    items=[
        ParsedItem(name="Milk", quantity="1", unit_price="5.00", category="Groceries"),
        ParsedItem(name="HST", quantity="1", unit_price="0.65", category="Tax & Fees"),
    ],
)


class FakeParser:
    def __init__(self, result: ParsedReceipt | None = GOOD, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.images: list[ReceiptImage | None] = []

    def parse(
        self,
        ocr_text: str,
        categories: list[str],
        default_currency: str,
        today: date,
        image: ReceiptImage | None = None,
    ) -> ParsedReceipt:
        self.images.append(image)
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture
def api() -> FakeSheetsApi:
    return FakeSheetsApi()


@pytest.fixture
def repository(api: FakeSheetsApi) -> SheetsRepository:
    return SheetsRepository(api, ensure_spreadsheet(api, "2026-10-13T00:00:00Z"), TabReadCache())


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser()


@pytest.fixture
def client(repository: SheetsRepository, parser: FakeParser) -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_receipt_parser] = lambda: parser
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_scan_returns_draft_with_strings_and_default_currency(client: TestClient) -> None:
    response = client.post("/api/receipts/scan", json={"ocr_text": "FRESHCO ..."})

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["currency"] == "CAD"
    assert body["draft"]["total"] == "5.65"
    assert body["draft"]["items"][1] == {
        "name": "HST",
        "quantity": "1",
        "price": "0.65",
        "category": "Tax & Fees",
        "subcategory": None,
    }
    assert [i["code"] for i in body["issues"]] == ["currency_defaulted"]
    assert body["needs_image"] is False


def test_scan_does_not_save_anything(client: TestClient, api: FakeSheetsApi) -> None:
    api.calls.clear()

    client.post("/api/receipts/scan", json={"ocr_text": "FRESHCO ..."})

    assert api.write_calls == []


def test_scan_retry_decodes_photo(client: TestClient, parser: FakeParser) -> None:
    image = base64.b64encode(b"\xff\xd8photo").decode()

    response = client.post(
        "/api/receipts/scan", json={"ocr_text": "x", "image": image, "image_media_type": "image/jpeg"}
    )

    assert response.json()["used_image"] is True
    assert parser.images == [[ReceiptImage(data=b"\xff\xd8photo", media_type="image/jpeg")]]


def test_scan_retry_with_several_photos_keeps_their_order(client: TestClient, parser: FakeParser) -> None:
    parts = [
        {"data": base64.b64encode(b"top").decode(), "media_type": "image/jpeg"},
        {"data": base64.b64encode(b"end").decode(), "media_type": "image/jpeg"},
    ]

    response = client.post("/api/receipts/scan", json={"ocr_text": "x", "images": parts})

    assert response.json()["used_image"] is True
    assert [image.data for image in parser.images[0]] == [b"top", b"end"]


@pytest.mark.parametrize(
    "body",
    [
        {"ocr_text": "x", "image": "aGVsbG8="},
        {"ocr_text": "x", "image": "not base64!", "image_media_type": "image/jpeg"},
        {"ocr_text": "x", "image": "aGVsbG8=", "image_media_type": "image/gif"},
        {"ocr_text": "x" * 60_001},
        {"ocr_text": "x", "images": [{"data": "aGVsbG8=", "media_type": "image/jpeg"}] * 6},
        {
            "ocr_text": "x",
            "image": "aGVsbG8=",
            "image_media_type": "image/jpeg",
            "images": [{"data": "aGVsbG8=", "media_type": "image/jpeg"}],
        },
    ],
)
def test_scan_rejects_bad_input(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/receipts/scan", json=body).status_code == 422


def test_scan_parse_failure_returns_502(client: TestClient, parser: FakeParser) -> None:
    parser.error = ReceiptParseError("refused")

    response = client.post("/api/receipts/scan", json={"ocr_text": "x"})

    assert response.status_code == 502
    assert "enter it by hand" in response.json()["detail"]


def test_scan_without_api_key_returns_503(repository: SheetsRepository) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(anthropic_api_key="")
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as client:
        assert client.post("/api/receipts/scan", json={"ocr_text": "x"}).status_code == 503
    app.dependency_overrides.clear()


def test_scan_requires_sign_in() -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(anthropic_api_key="key")
    with TestClient(app) as client:
        assert client.post("/api/receipts/scan", json={"ocr_text": "x"}).status_code == 401
    app.dependency_overrides.clear()


def create_receipt(client: TestClient) -> dict[str, Any]:
    body = {
        "store": "Fresh/Co: Downtown",
        "purchased_at": "2026-10-05",
        "total": "5.65",
        "items": [
            {"name": "Milk", "price": "5.00", "category": "Groceries"},
            {"name": "HST", "price": "0.65", "category": "Tax & Fees"},
        ],
    }
    response = client.post("/api/receipts", json=body)
    assert response.status_code == 201
    return dict(response.json())


def test_upload_image_stores_photo_in_receipts_folder(client: TestClient, api: FakeSheetsApi) -> None:
    receipt = create_receipt(client)

    response = client.put(
        f"/api/receipts/{receipt['receipt_id']}/image",
        content=b"\xff\xd8photo",
        headers={"Content-Type": "image/jpeg"},
    )

    assert response.status_code == 200
    link = response.json()["image_link"]
    assert link.startswith("https://drive.google.com/")
    file_id = link.split("/")[5]
    photo = api.files[file_id]
    assert photo.name == f"2026-10-05 Fresh Co Downtown {receipt['receipt_id'][:8]}.jpg"
    folder = api.files[photo.parent_id or ""]
    assert (folder.name, folder.marker) == ("receipts", "receipts-folder")
    assert api.files[folder.parent_id or ""].marker == FOLDER_MARKER
    assert api.uploads[file_id] == b"\xff\xd8photo"
    assert client.get(f"/api/receipts/{receipt['receipt_id']}").json()["image_link"] == link


def test_second_upload_reuses_receipts_folder(client: TestClient, api: FakeSheetsApi) -> None:
    receipt = create_receipt(client)
    url = f"/api/receipts/{receipt['receipt_id']}/image"
    client.put(url, content=b"one", headers={"Content-Type": "image/jpeg"})
    api.calls.clear()

    client.put(url, content=b"two", headers={"Content-Type": "image/png"})

    assert api.write_calls == ["upload_file", "write_values"]


@pytest.mark.parametrize(
    ("content", "content_type", "status"),
    [
        (b"x", "image/gif", 415),
        (b"x", "text/plain", 415),
        (b"", "image/jpeg", 413),
        (b"x" * (5 * 1024 * 1024 + 1), "image/jpeg", 413),
    ],
)
def test_upload_image_rejects_bad_files(
    client: TestClient, content: bytes, content_type: str, status: int
) -> None:
    receipt = create_receipt(client)

    response = client.put(
        f"/api/receipts/{receipt['receipt_id']}/image",
        content=content,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == status


def test_upload_image_for_missing_receipt_returns_404(client: TestClient) -> None:
    response = client.put("/api/receipts/missing/image", content=b"x", headers={"Content-Type": "image/jpeg"})

    assert response.status_code == 404
