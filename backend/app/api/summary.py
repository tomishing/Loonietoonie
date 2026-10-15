from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.services.spending_sources import spending_records
from app.services.summary import Summary, summarize
from app.storage.dependencies import get_repository
from app.storage.repository import Repository

router = APIRouter(prefix="/summary", tags=["summary"])


@router.get("", response_model=Summary)
def get_summary(
    repository: Annotated[Repository, Depends(get_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
    month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
) -> Summary:
    """Spending patterns for one month and one currency (defaults: this month, DEFAULT_CURRENCY).

    Includes card and bank charges that no receipt explains (phase 10b).
    """
    today = date.today()
    receipts = spending_records(repository)
    return summarize(
        receipts,
        month=month or today.strftime("%Y-%m"),
        currency=(currency or settings.default_currency).upper(),
        default_currency=settings.default_currency,
        today=today,
    )
