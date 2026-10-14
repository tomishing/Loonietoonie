"""Asks the real chatbot questions about known receipts. Costs a few cents per run (Claude Opus 5.5).

Run with:  RUN_LIVE_CLAUDE=1 .venv/bin/pytest tests/live -v -s
"""

import os
from datetime import date

import pytest

from app.ai.chat import ChatMessage, ClaudeChatRunner
from app.ai.receipt_parser import claude_client
from app.config import Settings
from app.services.spending_queries import SpendingQueries
from tests.test_spending_queries import RECEIPTS

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_CLAUDE") != "1", reason="set RUN_LIVE_CLAUDE=1 to call Claude"
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("How much did I spend on groceries in September 2026?", "14.98"),  # 5.49 + 4.00 + 5.49
        ("2026年9月の外食はいくらでしたか？", "18"),  # Dining Out: Ramen 18
        ("Which day of the week do I spend the most on?", "Saturday"),
    ],
)
def test_chat_answers_from_data(question: str, expected: str) -> None:
    settings = Settings()
    runner = ClaudeChatRunner(claude_client(settings.anthropic_api_key), settings.anthropic_chat_model)
    queries = SpendingQueries(RECEIPTS, default_currency="CAD", today=date(2026, 10, 13))

    events = list(
        runner.run(
            [ChatMessage(role="user", content=question)], queries, ["Groceries", "Dining Out", "Tax & Fees"]
        )
    )

    answer = "".join(e.text for e in events if e.type == "text")
    print(f"\nQ: {question}\nA: {answer}\nstatus: {[e.text for e in events if e.type == 'status']}")
    assert events[-1].type == "done"
    assert any(e.type == "status" for e in events), "expected at least one tool lookup"
    assert expected in answer
