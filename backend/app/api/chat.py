import logging
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.ai.chat import ChatEvent, ChatMessage, ChatRunner, ClaudeChatRunner
from app.ai.receipt_parser import claude_client
from app.api.limits import LimiterDep, check_daily_limit
from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.config import Settings, get_settings
from app.services.spending_queries import SpendingQueries
from app.services.spending_sources import spending_records
from app.storage.dependencies import get_repository
from app.storage.repository import Repository, StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 4_000
TITLE_CHARS = 80


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    # The saved conversation this question continues; none starts a new one.
    conversation_id: str | None = Field(default=None, max_length=64)

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
    user: Annotated[SessionData, Depends(get_current_user)],
    limiter: LimiterDep,
) -> StreamingResponse:
    """Stream an answer as newline-delimited JSON events: conversation (its ID, first), text,
    status, reset, error, done. A completed answer is saved with its question in the user's
    spreadsheet (chat_messages); a failed one isn't."""
    check_daily_limit(limiter, settings, user.sub, "ask")
    # Loaded before streaming starts, so storage errors become normal HTTP errors.
    queries = SpendingQueries(spending_records(repository), settings.default_currency, date.today())
    # "Groceries: Fresh Fruit & Vegetables; Bakery; …" so the model knows the exact names to filter by.
    # Semicolons, because a name may contain commas ("Rice, Grains & Cereal").
    categories = [
        f"{c.name}: {'; '.join(c.subcategories)}" if c.subcategories else c.name
        for c in repository.list_categories()
    ]

    conversation_id = body.conversation_id or str(uuid.uuid4())

    def events() -> Iterator[str]:
        yield ChatEvent("conversation", conversation_id).to_json() + "\n"
        answer = ""
        for event in runner.run(body.messages, queries, categories):
            if event.type == "text":
                answer += event.text
            elif event.type in ("reset", "error"):
                answer = ""  # an answer that was restarted or failed isn't kept
            elif event.type == "done" and answer.strip():
                try:
                    question = body.messages[-1].content
                    repository.append_chat_messages(
                        conversation_id, [("user", question), ("assistant", answer)]
                    )
                except StorageError:
                    logger.warning("Could not save an Ask conversation")
            yield event.to_json() + "\n"

    return StreamingResponse(
        events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
    )


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str  # the first question
    updated_at: str  # the last message's time
    message_count: int


class SavedMessage(BaseModel):
    role: str
    content: str
    created_at: str


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(
    repository: Annotated[Repository, Depends(get_repository)],
) -> list[ConversationSummary]:
    """Saved Ask conversations, most recent first."""
    grouped: dict[str, list[SavedMessage]] = {}
    last_saved: dict[str, int] = {}  # position of each conversation's latest row (same-second ties)
    for position, message in enumerate(repository.list_chat_messages()):
        grouped.setdefault(message.conversation_id, []).append(
            SavedMessage(role=message.role, content=message.content, created_at=message.created_at)
        )
        last_saved[message.conversation_id] = position
    summaries = []
    for conversation_id, messages in grouped.items():
        first = next((m.content for m in messages if m.role == "user"), "")
        title = " ".join(first.split())
        summaries.append(
            ConversationSummary(
                conversation_id=conversation_id,
                title=title if len(title) <= TITLE_CHARS else title[: TITLE_CHARS - 1] + "…",
                updated_at=messages[-1].created_at,
                message_count=len(messages),
            )
        )
    return sorted(summaries, key=lambda s: (s.updated_at, last_saved[s.conversation_id]), reverse=True)


@router.get("/conversations/{conversation_id}", response_model=list[SavedMessage])
def get_conversation(
    conversation_id: str, repository: Annotated[Repository, Depends(get_repository)]
) -> list[SavedMessage]:
    messages = [
        SavedMessage(role=m.role, content=m.content, created_at=m.created_at)
        for m in repository.list_chat_messages()
        if m.conversation_id == conversation_id
    ]
    if not messages:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return messages


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str, repository: Annotated[Repository, Depends(get_repository)]
) -> Response:
    """Delete a saved conversation (its rows are marked deleted in the spreadsheet)."""
    if repository.delete_conversation(conversation_id) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
