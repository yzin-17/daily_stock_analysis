"""Search.news 的隔离 HTTP 边界；不落 Cookie，不跟随重定向。"""

from contextlib import contextmanager
import time
from urllib.parse import urlsplit

from curl_cffi import requests

from data_provider.yahoo_news import MAX_BYTES, SEARCH_URL, YahooNewsError
MAX_HTTP_REQUESTS = 8
_TARGETS = {
    ("fc.yahoo.com", "/"), ("guce.yahoo.com", "/consent"),
    ("query1.finance.yahoo.com", "/v1/test/getcrumb"),
    ("query2.finance.yahoo.com", "/v1/finance/search"),
}


class YahooNewsSession(requests.Session):
    def __init__(self, connection, deadline, ticker, count):
        super().__init__(impersonate="chrome")
        self.connection, self.deadline = connection, deadline
        self.ticker, self.count = ticker, count
        self.calls, self.search_body, self.trace = 0, None, []

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        if (method.upper() != "GET" or parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port is not None or parsed.fragment or parsed.query
                or (parsed.hostname, parsed.path or "/") not in _TARGETS):
            raise YahooNewsError("configuration_failure")
        search = url == SEARCH_URL
        if search:
            params = kwargs.get("params", {})
            expected = {"q": self.ticker, "quotesCount": 1, "newsCount": self.count,
                        "enableFuzzyQuery": False, "listsCount": 0, "recommendedCount": 0,
                        "enableCb": False, "enableNavLinks": False, "enableResearchReports": False,
                        "enableCulturalAssets": False}
            if self.search_body is not None or any(params.get(k) != v for k, v in expected.items()):
                raise YahooNewsError("configuration_failure")
        if self.calls >= MAX_HTTP_REQUESTS:
            raise YahooNewsError("configuration_failure")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout("新闻总请求预算已耗尽")
        self.calls += 1
        kwargs.update(timeout=remaining, allow_redirects=False, stream=True)
        self.connection.send(("attempt", None))
        response = super().request(method, url, **kwargs)
        self.trace.append({"method": method.upper(), "path": parsed.netloc + (parsed.path or "/"),
                           "status": response.status_code, "kind": "search" if search else "bootstrap"})
        try:
            if search:
                self.connection.send(("http_status", response.status_code))
                if response.status_code != 200:
                    raise YahooNewsError("rate_limited" if response.status_code == 429 else "upstream_failure")
            body = bytearray()
            for chunk in response.iter_content():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise YahooNewsError()
            response.content = bytes(body)
            if search:
                self.search_body = response.content
            return response
        finally:
            response.close()


@contextmanager
def search_session(connection, deadline, ticker, count):
    import yfinance.cache as cache
    from yfinance.config import YfConfig
    cookie, tz = cache._CookieCacheManager._Cookie_cache, cache._TzCacheManager._tz_cache
    retries = YfConfig.network.retries
    cache._CookieCacheManager._Cookie_cache = cache._CookieCacheDummy()
    cache._TzCacheManager._tz_cache = cache._TzCacheDummy()
    YfConfig.network.retries = 0
    try:
        with YahooNewsSession(connection, deadline, ticker, count) as session:
            yield session
    finally:
        cache._CookieCacheManager._Cookie_cache, cache._TzCacheManager._tz_cache = cookie, tz
        YfConfig.network.retries = retries
