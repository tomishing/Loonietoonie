"""Turns the per-user Claude limits (app/services/usage.py) into a friendly 429."""

import math
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.config import Settings
from app.services.usage import Feature, LimitReachedError, UsageLimiter, get_usage_limiter

LimiterDep = Annotated[UsageLimiter, Depends(get_usage_limiter)]

# What was used up, and what the user can still do meanwhile.
_MESSAGES: dict[Feature, tuple[str, str]] = {
    "scan": ("receipt scans", "You can still enter receipts by hand."),
    "ask": ("Ask questions", "Your saved conversations are still here."),
    "import": ("statement imports", "Your imported transactions are all saved."),
}


def _wait(seconds: int) -> str:
    if seconds < 60 * 60:
        minutes = max(1, math.ceil(seconds / 60))
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = math.ceil(seconds / (60 * 60))
    return f"about {hours} hour{'s' if hours != 1 else ''}"


def check_daily_limit(limiter: UsageLimiter, settings: Settings, user_id: str, feature: Feature) -> None:
    """Count one Claude call for this user, or respond 429 when today's limit is used up."""
    try:
        limiter.take(user_id, feature, settings.daily_limit(feature))
    except LimitReachedError as exc:
        used, meanwhile = _MESSAGES[feature]
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You've used all {exc.limit} {used} for today. "
                f"You can try again in {_wait(exc.retry_after)}. {meanwhile}"
            ),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
