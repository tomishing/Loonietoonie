from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.ai.receipt_parser import claude_client
from app.api.limits import LimiterDep, check_daily_limit
from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.config import Settings, get_settings
from app.imports.categorizer import ClaudeMerchantCategorizer, MerchantCategorizer
from app.imports.parser import MAX_FILE_BYTES, ColumnMapping, FileTooLargeError, ImportFileError
from app.services import imports as service
from app.sheets.schema import utc_now_iso
from app.storage.dependencies import get_repository
from app.storage.models import BankAccount, ImportRecord
from app.storage.repository import Repository

router = APIRouter(tags=["imports"])

UserDep = Annotated[SessionData, Depends(get_current_user)]
RepositoryDep = Annotated[Repository, Depends(get_repository)]

# Base64 makes the file a third larger.
MAX_BASE64 = MAX_FILE_BYTES * 4 // 3 + 4


def get_categorizer(settings: Annotated[Settings, Depends(get_settings)]) -> MerchantCategorizer | None:
    """Claude suggests categories for new merchants; without an API key, imports use "Other"."""
    if not settings.anthropic_api_key:
        return None
    return ClaudeMerchantCategorizer(
        claude_client(settings.anthropic_api_key), settings.anthropic_receipt_model
    )


CategorizerDep = Annotated[MerchantCategorizer | None, Depends(get_categorizer)]


def _account_error(exc: Exception) -> HTTPException:
    if isinstance(exc, service.LinkedAccountError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This account is linked to your bank and updates itself. Import into another account.",
        )
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")


def _file_error(exc: ImportFileError) -> HTTPException:
    code = (
        status.HTTP_413_CONTENT_TOO_LARGE
        if isinstance(exc, FileTooLargeError)
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return HTTPException(code, detail=str(exc))


# --- accounts ------------------------------------------------------------------------------------


@router.get("/accounts", response_model=list[service.AccountInfo])
def list_accounts(user: UserDep, repository: RepositoryDep) -> list[service.AccountInfo]:
    """Bank and card accounts, linked and filled from statement files, with their last import."""
    return service.list_accounts(repository)


@router.post("/accounts", response_model=BankAccount, status_code=status.HTTP_201_CREATED)
def create_account(body: service.AccountDraft, user: UserDep, repository: RepositoryDep) -> BankAccount:
    return service.create_account(repository, body)


@router.put("/accounts/{account_id}", response_model=BankAccount)
def update_account(
    account_id: str, body: service.AccountDraft, user: UserDep, repository: RepositoryDep
) -> BankAccount:
    try:
        return service.update_account(repository, account_id, body)
    except (service.AccountNotFoundError, service.LinkedAccountError) as exc:
        raise _account_error(exc) from exc


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: str, user: UserDep, repository: RepositoryDep, remove_transactions: bool = False
) -> Response:
    """Remove an account. Its transactions stay unless `remove_transactions=true`."""
    try:
        service.delete_account(repository, account_id, remove_transactions)
    except (service.AccountNotFoundError, service.LinkedAccountError) as exc:
        raise _account_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- imports -------------------------------------------------------------------------------------


class ImportFile(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    filename: str = Field(default="", max_length=500)
    # The file's bytes, base64-encoded. CSV, OFX, or QFX, at most 2 MB.
    data: str = Field(min_length=1)
    # The column mapping the user set in the preview; omitted to detect it (or use the saved one).
    mapping: ColumnMapping | None = None


class ImportRequest(ImportFile):
    # Lines of possible duplicates the user chose to import anyway.
    include_lines: list[int] = Field(default_factory=list, max_length=5_000)


def _decode(body: ImportFile) -> bytes:
    if len(body.data) > MAX_BASE64:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail="The file is larger than 2 MB. Download a shorter date range.",
        )
    try:
        return service.decode_file(body.data)
    except ImportFileError as exc:
        raise _file_error(exc) from exc


@router.post("/imports/preview", response_model=service.ImportPreview)
def preview(body: ImportFile, user: UserDep, repository: RepositoryDep) -> service.ImportPreview:
    """Read a statement file and show what an import would add. Nothing is saved."""
    data = _decode(body)
    try:
        return service.preview_import(repository, body.account_id, data, body.mapping)
    except (service.AccountNotFoundError, service.LinkedAccountError) as exc:
        raise _account_error(exc) from exc
    except ImportFileError as exc:
        raise _file_error(exc) from exc


@router.post("/imports", response_model=service.ImportResult, status_code=status.HTTP_201_CREATED)
def run_import(
    body: ImportRequest,
    user: UserDep,
    repository: RepositoryDep,
    categorizer: CategorizerDep,
    settings: Annotated[Settings, Depends(get_settings)],
    limiter: LimiterDep,
) -> service.ImportResult:
    """Import a statement file's new transactions into an account."""
    data = _decode(body)
    if categorizer is not None:  # imports call Claude only to categorize new merchants
        check_daily_limit(limiter, settings, user.sub, "import")
    try:
        return service.run_import(
            repository,
            categorizer,
            user.sub,
            body.account_id,
            body.filename,
            data,
            body.mapping,
            set(body.include_lines),
            utc_now_iso(),
        )
    except (service.AccountNotFoundError, service.LinkedAccountError) as exc:
        raise _account_error(exc) from exc
    except ImportFileError as exc:
        raise _file_error(exc) from exc


@router.get("/imports", response_model=list[ImportRecord])
def list_imports(
    user: UserDep, repository: RepositoryDep, account_id: str | None = None
) -> list[ImportRecord]:
    """Imported statement files, newest first."""
    return [r for r in repository.list_imports() if account_id is None or r.account_id == account_id]


@router.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def undo_import(import_id: str, user: UserDep, repository: RepositoryDep) -> Response:
    """Undo an import: its transactions are marked deleted."""
    try:
        service.undo_import(repository, user.sub, import_id)
    except service.ImportNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Import not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
