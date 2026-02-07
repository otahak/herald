"""Simple in-memory rate limiter for destructive or expensive endpoints."""

import time
from collections import defaultdict
from threading import Lock

# key -> list of timestamps (pruned to window)
_entries: dict[str, list[float]] = defaultdict(list)
_lock = Lock()

# Default: 10 requests per 60 seconds per key
DEFAULT_MAX = 10
DEFAULT_WINDOW_SEC = 60


def check_rate_limit(key: str, max_requests: int = DEFAULT_MAX, window_sec: float = DEFAULT_WINDOW_SEC) -> bool:
    """
    Check if the key is over the rate limit. If not, record this request and return True.
    If over limit, return False (caller should return 429).
    """
    now = time.monotonic()
    cutoff = now - window_sec
    with _lock:
        times = _entries[key]
        times[:] = [t for t in times if t > cutoff]
        if len(times) >= max_requests:
            return False
        times.append(now)
    return True
