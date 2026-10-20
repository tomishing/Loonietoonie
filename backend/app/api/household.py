from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.services import household as service
from app.services.spending_sources import spending_records
from app.storage.dependencies import get_repository
from app.storage.repository import Repository

router = APIRouter(prefix="/household", tags=["household"])

RepositoryDep = Annotated[Repository, Depends(get_repository)]


@router.get("", response_model=service.HouseholdSettings)
def get_household(repository: RepositoryDep) -> service.HouseholdSettings:
    """Who lives in the household and where (for "Compared with similar households"), with the choices."""
    return service.settings_view(repository.get_settings())


@router.put("", response_model=service.HouseholdSettings)
def save_household(body: service.Household, repository: RepositoryDep) -> service.HouseholdSettings:
    if body.household_type not in service.HOUSEHOLD_TYPES or body.province not in service.PROVINCE_LABELS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Choose a household and a province."
        )
    repository.save_settings(
        {service.HOUSEHOLD_TYPE_KEY: body.household_type, service.PROVINCE_KEY: body.province}
    )
    return service.settings_view(repository.get_settings())


@router.delete("", response_model=service.HouseholdSettings)
def clear_household(repository: RepositoryDep) -> service.HouseholdSettings:
    """Forget the household type and province (Patterns stops comparing until they're set again)."""
    repository.save_settings({service.HOUSEHOLD_TYPE_KEY: "", service.PROVINCE_KEY: ""})
    return service.settings_view(repository.get_settings())


@router.get("/comparison", response_model=service.Comparison)
def get_comparison(repository: RepositoryDep) -> service.Comparison:
    """The user's monthly spending per category next to similar households' (Statistics Canada)."""
    return service.compare(spending_records(repository), repository.get_settings(), date.today())
