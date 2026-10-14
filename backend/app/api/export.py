from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.services.export import ExportKind, export_csv
from app.storage.dependencies import get_repository
from app.storage.models import ReceiptFilter
from app.storage.repository import Repository

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/csv")
def export_receipts_csv(
    repository: Annotated[Repository, Depends(get_repository)],
    kind: ExportKind = "items",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    category: str | None = None,
    subcategory: str | None = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
) -> Response:
    """Download receipts as CSV: one row per item (default) or per receipt.

    Same filters as GET /api/receipts, plus an optional currency.
    """
    filters = ReceiptFilter(date_from=date_from, date_to=date_to, category=category, subcategory=subcategory)
    receipts = repository.list_receipts(filters)
    if currency:
        receipts = [r for r in receipts if r.currency == currency.upper()]
    filename = f"loonietoonie-{kind}-{date.today().isoformat()}.csv"
    return Response(
        content=export_csv(receipts, kind, filters).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
