from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.ai.chat import ChatMessage, ChatRunner, ClaudeChatRunner
from app.ai.receipt_parser import claude_client
from app.config import Settings, get_settings
from app.services.spending_queries import SpendingQueries
from app.services.spending_sources import spending_records
from app.storage.dependencies import get_repository
from app.storage.repository import Repository

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 4_000


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)

    @field_validator("messages")
    @classmethod
    def _well_formed(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if messages[-1].role != "user":
            raise ValueError("The last message must be from the user")
        if any(not m.content.strip() or len(m.content) > MAX_MESSAGE_CHARS for m in messages):
            raise ValueError(f"Messages must be 1-{MAX_MESSAGE_CHARS} characters")
        return messages


def get_chat_runner(settings: Annotated[Settings, Depends(get_settings)]) -> ChatRunner:
    if not settings.anthropic_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="The assistant is not configured on the server."
        )
    return ClaudeChatRunner(claude_client(settings.anthropic_api_key), settings.anthropic_chat_model)


@router.post("")
def chat(
    body: ChatRequest,
    repository: Annotated[Repository, Depends(get_repository)],
    runner: Annotated[ChatRunner, Depends(get_chat_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Stream an answer as newline-delimited JSON events: text, status, reset, error, done."""
    # Loaded before streaming starts, so storage errors become normal HTTP errors.
    queries = SpendingQueries(spending_records(repository), settings.default_currency, date.today())
    # "Groceries: Fresh Fruit & Vegetables; Bakery; …" so the model knows the exact names to filter by.
    # Semicolons, because a name may contain commas ("Rice, Grains & Cereal").
    categories = [
        f"{c.name}: {'; '.join(c.subcategories)}" if c.subcategories else c.name
        for c in repository.list_categories()
    ]

    def events() -> Iterator[str]:
        for event in runner.run(body.messages, queries, categories):
            yield event.to_json() + "\n"

    return StreamingResponse(
        events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
    )
