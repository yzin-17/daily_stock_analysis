"""Tushare 精确历史读取的单次请求预算，不隐式等待额度或重试。"""

import math
import time

from .base import DataFetchError, RateLimitError


class TushareHistoryRequest:
    def __init__(self, fetcher, timeout_seconds):
        if (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("invalid Tushare request timeout")
        if not fetcher._token or fetcher._api is None:
            raise DataFetchError("Tushare credential unavailable")
        self.fetcher = fetcher
        self.deadline = time.monotonic() + timeout_seconds
        self.client = fetcher._build_api_client(fetcher._token)
        self.client._source_pinned = True

    def before_call(self):
        self.check_complete()
        fetcher = self.fetcher
        if (
            fetcher._minute_start is not None and time.time() - fetcher._minute_start < 60
            and fetcher._call_count >= fetcher.rate_limit_per_minute
        ):
            raise RateLimitError("Tushare history rate limit exceeded")
        fetcher._check_rate_limit()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Tushare history deadline exceeded")
        self.client._timeout = remaining

    def check_complete(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Tushare history deadline exceeded")
