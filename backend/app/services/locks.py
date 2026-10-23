"""One bank-data change at a time per user (bank sync, statement import): two at once could
append the same new rows twice."""

import threading
from collections import defaultdict

_user_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


def user_lock(user_id: str) -> threading.Lock:
    return _user_locks[user_id]
