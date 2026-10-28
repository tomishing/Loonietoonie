import pytest

from app.api.limits import _wait
from app.services.usage import WINDOW_SECONDS, LimitReachedError, UsageLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_counts_calls_up_to_the_limit() -> None:
    limiter = UsageLimiter(Clock())
    limiter.take("u1", "scan", 2)
    limiter.take("u1", "scan", 2)

    with pytest.raises(LimitReachedError) as raised:
        limiter.take("u1", "scan", 2)
    assert (raised.value.feature, raised.value.limit) == ("scan", 2)


def test_users_and_features_are_counted_separately() -> None:
    limiter = UsageLimiter(Clock())
    limiter.take("u1", "scan", 1)

    limiter.take("u2", "scan", 1)
    limiter.take("u1", "ask", 1)
    limiter.take("u1", "import", 1)


def test_a_call_leaves_the_window_after_24_hours() -> None:
    clock = Clock()
    limiter = UsageLimiter(clock)
    limiter.take("u1", "ask", 2)
    clock.now += 3_600
    limiter.take("u1", "ask", 2)

    clock.now += WINDOW_SECONDS - 3_600 - 10
    with pytest.raises(LimitReachedError) as raised:
        limiter.take("u1", "ask", 2)
    assert raised.value.retry_after == 10  # until the first call is 24 hours old

    clock.now += 10
    limiter.take("u1", "ask", 2)
    with pytest.raises(LimitReachedError):
        limiter.take("u1", "ask", 2)  # the second call is still in the window


def test_a_refused_call_is_not_counted() -> None:
    clock = Clock()
    limiter = UsageLimiter(clock)
    limiter.take("u1", "scan", 1)
    for _ in range(5):
        with pytest.raises(LimitReachedError):
            limiter.take("u1", "scan", 1)

    clock.now += WINDOW_SECONDS
    limiter.take("u1", "scan", 1)


@pytest.mark.parametrize("limit", [0, -1])
def test_zero_turns_the_limit_off(limit: int) -> None:
    limiter = UsageLimiter(Clock())
    for _ in range(100):
        limiter.take("u1", "scan", limit)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (1, "about 1 minute"),
        (61, "about 2 minutes"),
        (3_599, "about 60 minutes"),
        (3_600, "about 1 hour"),
        (3_601, "about 2 hours"),
        (WINDOW_SECONDS, "about 24 hours"),
    ],
)
def test_wait_is_said_in_minutes_or_hours(seconds: int, text: str) -> None:
    assert _wait(seconds) == text
