import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import auth, categories, chat, health, receipts, scan, summary
from app.config import get_settings
from app.storage.repository import StorageError

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LoonieToonie API", version=__version__)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(categories.router, prefix="/api")
    app.include_router(summary.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(scan.router, prefix="/api")
    app.include_router(receipts.router, prefix="/api")

    @app.exception_handler(StorageError)
    def storage_error_handler(request: Request, exc: StorageError) -> JSONResponse:
        logger.warning("Storage error on %s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Couldn't reach your LoonieToonie spreadsheet. Please try again."},
        )

    return app


app = create_app()
