"""隔离搜索执行中的实际 HTTP 尝试与来源原始元数据观察。"""

from contextlib import contextmanager
import time
from weakref import WeakSet

import requests

from src.services.thesis_ledger_news_identity import news_url
from src.services.thesis_ledger_news_records import RawNewsRecord

_PATHS = {
    "tavily": ("results",), "serpapi": ("organic_results",),
    "bocha": ("data", "webPages", "value"), "anspire": ("results",),
    "minimax": ("organic",), "brave": ("web", "results"), "searxng": ("results",),
}


class NewsTransportObserver:
    def __init__(self, connection, settings, deadline):
        self.connection, self.settings, self.deadline = connection, settings, deadline
        self.documents = {}
        self.valid_payload = False
        self.truncated = False
        self.partial = False
        self.invalid_payload = False
        self.examined_count = 0
        self._responses = WeakSet()

    def observe_payload(self, response):
        if response in self._responses:
            return
        self._responses.add(response)
        if response.status_code != 200:
            return
        try:
            payload = response.json()
            rows = payload
            for key in _PATHS[self.settings.provider_id]:
                rows = rows[key]
            if not isinstance(rows, list):
                raise ValueError("来源记录必须为列表")
            self.valid_payload = True
            self.examined_count += len(rows)
            if isinstance(payload, dict):
                self.partial = self.partial or bool(payload.get("unresponsive_engines"))
                total = payload.get("number_of_results")
                self.truncated = self.truncated or bool(payload.get("hasMore") or payload.get("nextCursor"))
                if isinstance(total, int) and not isinstance(total, bool) and total > len(rows):
                    self.truncated = True
            for row in rows:
                if not isinstance(row, dict):
                    self.invalid_payload = True
                    continue
                url = row.get("url") or row.get("link")
                title = row.get("title") or row.get("name")
                if not isinstance(title, str) or not title.strip():
                    self.invalid_payload = True
                if isinstance(url, str):
                    try:
                        self.documents[news_url(url)] = row
                    except ValueError:
                        self.invalid_payload = True
                else:
                    self.invalid_payload = True
        except (ValueError, KeyError, TypeError):
            self.invalid_payload = True

    @contextmanager
    def capture(self):
        original = requests.sessions.Session.send
        observer = self

        def send(session, request, **kwargs):
            try:
                permitted = observer.settings.permits_url(request.url)
            except ValueError:
                permitted = False
            if not permitted:
                observer.connection.send(("configuration_failure", None))
                raise requests.RequestException("新闻来源请求目标不匹配")
            remaining = observer.deadline - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout("新闻总请求预算已耗尽")
            timeout = kwargs.get("timeout")
            if isinstance(timeout, tuple):
                kwargs["timeout"] = tuple(min(value or remaining, remaining) for value in timeout)
            elif isinstance(timeout, (int, float)):
                kwargs["timeout"] = min(timeout, remaining)
            else:
                kwargs["timeout"] = remaining
            observer.connection.send(("attempt", None))
            try:
                response = original(session, request, **kwargs)
            except requests.Timeout:
                observer.connection.send(("timeout", None))
                raise
            except requests.RequestException:
                observer.connection.send(("upstream_failure", None))
                raise
            observer.connection.send(("http_status", response.status_code))
            observer.observe_payload(response)
            return response

        requests.sessions.Session.send = send
        try:
            yield
        finally:
            requests.sessions.Session.send = original

    def records(self, results):
        records = []
        for result in results:
            try:
                document = self.documents.get(news_url(result.url), {})
            except ValueError:
                document = {}
            published = next((document.get(key) for key in
                              ("published_date", "publishedDate", "datePublished", "page_age", "date", "age")
                              if document.get(key) is not None), result.published_date)
            records.append(RawNewsRecord(
                title=result.title, url=result.url, excerpt=result.snippet,
                publisher=document.get("publisher") if isinstance(document.get("publisher"), str) else result.source,
                source_item_id=_text(document.get("sourceItemId", document.get("id"))),
                original_url=_text(document.get("originalUrl", document.get("original_url"))),
                published_at=_text(published),
                source_available_at=_text(document.get("sourceAvailableAt", document.get("source_available_at"))),
            ))
        return tuple(records)


def _text(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value) or None
    return None
