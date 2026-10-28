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
from app.api.limits import LimiterDep, check_daily_limit
from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.config import Settings, get_settings
from app.services.scan import ScanResult, scan_receipt
from app.storage.dependencies import get_repository
from app.storage.repository import Repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/receipts", tags=["scan"])

# Claude accepts images up to 5 MB; the app downsizes photos well below that.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# A long receipt can be photographed in up to this many parts (the document scanner's page limit).
MAX_IMAGES = 5
MAX_TOTAL_IMAGE_BYTES = 15 * 1024 * 1024
MAX_BASE64 = (MAX_IMAGE_BYTES * 4) // 3 + 4


class ScanImage(BaseModel):
    data: str = Field(max_length=MAX_BASE64)  # base64
    media_type: ImageMediaType


class ScanRequest(BaseModel):
    ocr_text: str = Field(default="", max_length=60_000)
    # Photos are sent only on the retry the response asks for with `needs_image`: either one
    # photo (`image` + `image_media_type`) or the parts of a long receipt in order (`images`).
    image: str | None = Field(default=None, max_length=MAX_BASE64)
    image_media_type: ImageMediaType | None = None
    images: list[ScanImage] = Field(default_factory=list, max_length=MAX_IMAGES)


def get_receipt_parser(settings: Annotated[Settings, Depends(get_settings)]) -> ReceiptParser:
    if not settings.anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Receipt scanning is not configured on the server."
        )
    return ClaudeReceiptParser(claude_client(settings.anthropic_api_key), settings.anthropic_receipt_model)


def _decode(data: str, media_type: ImageMediaType) -> ReceiptImage:
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="image is not valid base64"
        ) from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Each photo must be under 5 MB")
    return ReceiptImage(data=raw, media_type=media_type)


def _decode_images(body: ScanRequest) -> list[ReceiptImage]:
    if body.image is not None and body.images:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Send image or images, not both")
    if body.image is not None:
        if body.image_media_type is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="image_media_type is required with image"
            )
        return [_decode(body.image, body.image_media_type)]
    images = [_decode(image.data, image.media_type) for image in body.images]
    if sum(len(image.data) for image in images) > MAX_TOTAL_IMAGE_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The photos are too large together")
    return images


@router.post("/scan", response_model=ScanResult)
def scan(
    body: ScanRequest,
    repository: Annotated[Repository, Depends(get_repository)],
    parser: Annotated[ReceiptParser, Depends(get_receipt_parser)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[SessionData, Depends(get_current_user)],
    limiter: LimiterDep,
) -> ScanResult:
    """Read a receipt into a draft for the review screen. Nothing is saved."""
    images = _decode_images(body)
    check_daily_limit(limiter, settings, user.sub, "scan")
    try:
        return scan_receipt(
            parser, repository, body.ocr_text, images, settings.default_currency, date.today()
        )
    except ReceiptParseError as exc:
        logger.warning("Receipt scan failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't read this receipt automatically. Try again, or enter it by hand.",
        ) from exc
