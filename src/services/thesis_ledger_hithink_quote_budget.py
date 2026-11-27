"""同一 HiThink 凭据的股票和 ETF 快照预算。"""

from __future__ import annotations

import math
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from hashlib import sha256
from threading import Lock
from time import monotonic, time

from requests.structures import CaseInsensitiveDict


class HiThinkQuoteBudgetUnavailable(Exception):
    """当前凭据的共享预算暂不可用。"""


def _nonnegative_number(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw is not None else -1
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


class HiThinkQuoteBudget:
    def __init__(self, *, clock=monotonic, wall_clock=time) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = Lock()
        self._remaining: float | None = None
        self._resume_at: float | None = None
        self._fallback_delay = 1.0

    @contextmanager
    def request(self, timeout_seconds: float):
        started = self._clock()
        if not self._lock.acquire(timeout=timeout_seconds):
            raise HiThinkQuoteBudgetUnavailable
        try:
            now = self._clock()
            timeout_left = timeout_seconds - (now - started)
            if timeout_left <= 0 or (self._resume_at is not None and now < self._resume_at):
                raise HiThinkQuoteBudgetUnavailable
            if self._resume_at is not None:
                self._resume_at = None
                self._remaining = None
            if self._remaining is not None:
                self._remaining -= 1
            yield timeout_left
        finally:
            if self._remaining is not None and self._remaining < 1 and self._resume_at is None:
                self._defer(None)
            self._lock.release()

    def observe(self, raw_headers, *, limited: bool = False) -> None:
        headers = CaseInsensitiveDict(raw_headers or {})
        limit = _nonnegative_number(headers.get("X-RateLimit-Limit"))
        remaining = _nonnegative_number(headers.get("X-RateLimit-Remaining"))
        if remaining is not None and (limit is None or remaining <= limit):
            self._remaining = remaining
        delay = self._retry_after(headers.get("Retry-After"))
        if limited or (self._remaining is not None and self._remaining < 1):
            self._defer(delay)
        else:
            self._fallback_delay = 1.0

    def _defer(self, delay: float | None) -> None:
        if delay is None:
            delay = self._fallback_delay
            self._fallback_delay = min(self._fallback_delay * 2, 60)
        self._resume_at = self._clock() + delay

    def _retry_after(self, value: str | None) -> float | None:
        seconds = _nonnegative_number(value)
        if seconds is not None or not value:
            return seconds
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is not None:
                return max(0, date.timestamp() - self._wall_clock())
        except (TypeError, ValueError, OverflowError, OSError):
            pass
        return None


_budgets: dict[bytes, HiThinkQuoteBudget] = {}
_budgets_lock = Lock()


def hithink_quote_budget(api_key: str) -> HiThinkQuoteBudget:
    identity = sha256(api_key.encode()).digest()
    with _budgets_lock:
        if identity not in _budgets:
            _budgets[identity] = HiThinkQuoteBudget()
        return _budgets[identity]
