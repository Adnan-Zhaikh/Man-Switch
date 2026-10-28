"""Small in-memory sliding-window rate limiter.

State lives in this process only: it resets on restart and isn't shared between
workers. That's fine for the single-instance setup this app targets (see README).
"""
import threading
import time
from collections import deque

_REGISTRY = []
_SWEEP_THRESHOLD = 5000


class RateLimiter:
    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self._hits = {}
        self._lock = threading.Lock()
        _REGISTRY.append(self)

    def _prune(self, key, now):
        q = self._hits.get(key)
        if q is None:
            return None
        while q and q[0] <= now - self.window:
            q.popleft()
        if not q:
            del self._hits[key]
            return None
        return q

    def _sweep(self, now):
        if len(self._hits) > _SWEEP_THRESHOLD:
            for key in list(self._hits):
                self._prune(key, now)

    def _retry_after(self, q, now):
        return max(1, int(q[0] + self.window - now) + 1)

    def check(self, key):
        """(allowed, retry_after_seconds) without recording anything."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if q is not None and len(q) >= self.limit:
                return False, self._retry_after(q, now)
            return True, 0

    def record(self, key):
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            self._hits.setdefault(key, deque()).append(now)

    def hit(self, key):
        """Record an attempt if allowed. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            q = self._prune(key, now)
            if q is not None and len(q) >= self.limit:
                return False, self._retry_after(q, now)
            self._hits.setdefault(key, deque()).append(now)
            return True, 0

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def clear(self):
        with self._lock:
            self._hits.clear()


def clear_all():
    for limiter in _REGISTRY:
        limiter.clear()
