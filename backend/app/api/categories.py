from typing import Annotated

from fastapi import APIRouter, Depends

from app.storage.dependencies import get_repository
from app.storage.models import Category
from app.storage.repository import Repository

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[Category])
def list_categories(repository: Annotated[Repository, Depends(get_repository)]) -> list[Category]:
    return repository.list_categories()
