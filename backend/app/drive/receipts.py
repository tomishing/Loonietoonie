"""Receipt photos in the user's Drive: LoonieToonie/receipts/."""

import re

from app.sheets.api import SheetsApi
from app.sheets.schema import FOLDER_MARKER, FOLDER_MIME_TYPE, FOLDER_NAME
from app.storage.models import Receipt

RECEIPTS_FOLDER_NAME = "receipts"
RECEIPTS_FOLDER_MARKER = "receipts-folder"
RECEIPT_IMAGE_MARKER = "receipt-image"

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def receipts_folder_id(api: SheetsApi) -> str:
    """Find or create LoonieToonie/receipts/ (and LoonieToonie/ itself if it is gone)."""
    existing = api.find_file_ids(RECEIPTS_FOLDER_MARKER, FOLDER_MIME_TYPE)
    if existing:
        return existing[0]
    roots = api.find_file_ids(FOLDER_MARKER, FOLDER_MIME_TYPE)
    root_id = roots[0] if roots else api.create_file(FOLDER_NAME, FOLDER_MIME_TYPE, FOLDER_MARKER, None)
    return api.create_file(RECEIPTS_FOLDER_NAME, FOLDER_MIME_TYPE, RECEIPTS_FOLDER_MARKER, root_id)


def image_file_name(receipt: Receipt, media_type: str) -> str:
    """E.g. "2026-10-05 FreshMart 1a2b3c4d.jpg". Characters Drive users dislike are removed."""
    store = re.sub(r"[\\/:*?\"<>|\s]+", " ", receipt.store).strip()[:60] or "receipt"
    return f"{receipt.purchased_at.isoformat()} {store} {receipt.receipt_id[:8]}.{EXTENSIONS[media_type]}"


def upload_receipt_image(api: SheetsApi, receipt: Receipt, data: bytes, media_type: str) -> str:
    """Upload the photo and return its Drive link."""
    return api.upload_file(
        image_file_name(receipt, media_type), media_type, data, receipts_folder_id(api), RECEIPT_IMAGE_MARKER
    )
