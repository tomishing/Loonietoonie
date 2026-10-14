from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.services import receipts as service
from app.storage.dependencies import get_repository
from app.storage.models import Receipt, ReceiptDraft, ReceiptFilter
from app.storage.repository import Repository

router = APIRouter(prefix="/receipts", tags=["receipts"])

RepositoryDep = Annotated[Repository, Depends(get_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

NOT_FOUND = "Receipt not found"
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _with_default_currency(draft: ReceiptDraft, settings: Settings) -> ReceiptDraft:
    if "currency" in draft.model_fields_set:
        return draft
    return draft.model_copy(update={"currency": settings.default_currency})


@router.get("", response_model=list[Receipt])
def list_receipts(
    repository: RepositoryDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> list[Receipt]:
    filters = ReceiptFilter(date_from=date_from, date_to=date_to, category=category, subcategory=subcategory)
    return service.list_receipts(repository, filters)


@router.post("", response_model=Receipt, status_code=status.HTTP_201_CREATED)
def create_receipt(draft: ReceiptDraft, repository: RepositoryDep, settings: SettingsDep) -> Receipt:
    try:
        return service.create_receipt(repository, _with_default_currency(draft, settings))
    except service.ReceiptValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.errors) from exc


@router.get("/{receipt_id}", response_model=Receipt)
def get_receipt(receipt_id: str, repository: RepositoryDep) -> Receipt:
    try:
        return service.get_receipt(repository, receipt_id)
    except service.ReceiptNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from exc


@router.put("/{receipt_id}", response_model=Receipt)
def update_receipt(
    receipt_id: str, draft: ReceiptDraft, repository: RepositoryDep, settings: SettingsDep
) -> Receipt:
    try:
        return service.update_receipt(repository, receipt_id, _with_default_currency(draft, settings))
    except service.ReceiptNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from exc
    except service.ReceiptValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.errors) from exc


@router.put("/{receipt_id}/image", response_model=Receipt)
async def upload_receipt_image(receipt_id: str, request: Request, repository: RepositoryDep) -> Receipt:
    """Save the receipt photo to Drive (LoonieToonie/receipts/). Body: the image bytes."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in IMAGE_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Send a JPEG, PNG, or WebP image")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="The photo must be under 5 MB")
    data = await request.body()
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="The photo must be under 5 MB")
    try:
        return await run_in_threadpool(service.attach_image, repository, receipt_id, data, media_type)
    except service.ReceiptNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from exc


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_receipt(receipt_id: str, repository: RepositoryDep) -> Response:
    try:
        service.delete_receipt(repository, receipt_id)
    except service.ReceiptNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
