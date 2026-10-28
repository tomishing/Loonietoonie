"""Per-user daily limits on Claude calls (receipt scans, Ask questions, statement imports), so one
account can't run up the Anthropic bill.

Counted in process memory over a rolling 24 hours, like the sync lock: the backend runs as one
instance, and a restart forgets the counts, which only errs in the user's favour."""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Literal

Feature = Literal["scan", "ask", "import"]

WINDOW_SECONDS = 24 * 60 * 60


class LimitReachedError(Exception):
    def __init__(self, feature: Feature, limit: int, retry_after: int) -> None:
        super().__init__(f"{feature} limit of {limit} reached")
        self.feature = feature
        self.limit = limit
        self.retry_after = retry_after  # seconds until the oldest counted call leaves the window


class UsageLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._calls: defaultdict[tuple[str, Feature], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def take(self, user_id: str, feature: Feature, limit: int) -> None:
        """Count one call, or raise LimitReachedError when the last 24 hours already hold `limit`.
        A limit of 0 or less turns the check off."""
        if limit <= 0:
            return
        now = self._clock()
        with self._lock:
            calls = self._calls[(user_id, feature)]
            while calls and calls[0] <= now - WINDOW_SECONDS:
                calls.popleft()
            if len(calls) >= limit:
                retry_after = max(1, math.ceil(calls[0] + WINDOW_SECONDS - now))
                raise LimitReachedError(feature, limit, retry_after)
            calls.append(now)


_limiter = UsageLimiter()


def get_usage_limiter() -> UsageLimiter:
    return _limiter
