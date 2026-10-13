from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.config import Settings, get_settings
from app.services import receipts as service
from app.storage.dependencies import get_repository
from app.storage.models import Receipt, ReceiptDraft, ReceiptFilter
from app.storage.repository import Repository

router = APIRouter(prefix="/receipts", tags=["receipts"])

RepositoryDep = Annotated[Repository, Depends(get_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

NOT_FOUND = "Receipt not found"


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
) -> list[Receipt]:
    filters = ReceiptFilter(date_from=date_from, date_to=date_to, category=category)
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


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_receipt(receipt_id: str, repository: RepositoryDep) -> Response:
    try:
        service.delete_receipt(repository, receipt_id)
    except service.ReceiptNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
