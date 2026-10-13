import base64
import binascii
import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.receipt_parser import (
    ClaudeReceiptParser,
    ImageMediaType,
    ReceiptImage,
    ReceiptParseError,
    ReceiptParser,
    claude_client,
)
from app.config import Settings, get_settings
from app.services.scan import ScanResult, scan_receipt
from app.storage.dependencies import get_repository
from app.storage.repository import Repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/receipts", tags=["scan"])

# Claude accepts images up to 5 MB; the app downsizes photos well below that.
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ScanRequest(BaseModel):
    ocr_text: str = Field(default="", max_length=20_000)
    # Base64 photo. Sent only on the retry the response asks for with `needs_image`.
    image: str | None = Field(default=None, max_length=(MAX_IMAGE_BYTES * 4) // 3 + 4)
    image_media_type: ImageMediaType | None = None


def get_receipt_parser(settings: Annotated[Settings, Depends(get_settings)]) -> ReceiptParser:
    if not settings.anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Receipt scanning is not configured on the server."
        )
    return ClaudeReceiptParser(claude_client(settings.anthropic_api_key), settings.anthropic_receipt_model)


def _decode_image(body: ScanRequest) -> ReceiptImage | None:
    if body.image is None:
        return None
    if body.image_media_type is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="image_media_type is required with image"
        )
    try:
        data = base64.b64decode(body.image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="image is not valid base64"
        ) from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The photo must be under 5 MB")
    return ReceiptImage(data=data, media_type=body.image_media_type)


@router.post("/scan", response_model=ScanResult)
def scan(
    body: ScanRequest,
    repository: Annotated[Repository, Depends(get_repository)],
    parser: Annotated[ReceiptParser, Depends(get_receipt_parser)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ScanResult:
    """Read a receipt into a draft for the review screen. Nothing is saved."""
    image = _decode_image(body)
    try:
        return scan_receipt(parser, repository, body.ocr_text, image, settings.default_currency, date.today())
    except ReceiptParseError as exc:
        logger.warning("Receipt scan failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't read this receipt automatically. Try again, or enter it by hand.",
        ) from exc
