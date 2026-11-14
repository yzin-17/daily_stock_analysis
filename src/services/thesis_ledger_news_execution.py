"""有总预算的单来源新闻执行；超时回收实际取数进程。"""

from contextlib import redirect_stderr, redirect_stdout
import logging
import multiprocessing
import os
from threading import BoundedSemaphore
import time
from typing import Protocol

from src.services.thesis_ledger_news_contract_v1 import NewsError
from src.services.thesis_ledger_news_records import RawNewsRead


class NewsExecutionSettings(Protocol):
    provider_id: str

    @property
    def result_limit(self) -> int | None:
        ...

    def configured(self) -> bool:
        ...


_NEWS_WORKER_SLOTS = BoundedSemaphore(4)

_MESSAGES = {
    "missing_credentials": "新闻来源缺少凭据或专属地址", "configuration_failure": "新闻来源请求配置不匹配",
    "unsupported": "当前环境无法执行该新闻来源或分页", "timeout": "新闻检索超过总请求预算或来源超时",
    "rate_limited": "新闻来源返回限流", "upstream_failure": "新闻来源请求或响应未完成",
}


def _error(code):
    return NewsError(code=code, message=_MESSAGES[code])


def news_search_worker(connection, settings, query, max_results, days, deadline):
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            from src.services.thesis_ledger_news_sources import build_news_source
            from src.services.thesis_ledger_news_transport import NewsTransportObserver
            observer = NewsTransportObserver(connection, settings, deadline)
            source = build_news_source(settings)
            with observer.capture():
                kwargs = {"topic": getattr(settings, "topic", "news")} if settings.provider_id == "tavily" else {}
                result = source.search(query, max_results=max_results, days=days, **kwargs)
            rows = observer.records(result.results)
            connection.send(("result", {
                "records": rows, "success": bool(result.success and observer.valid_payload),
                "truncated": observer.truncated or len(rows) >= max_results,
                "examined_count": observer.examined_count, "partial": observer.partial or observer.invalid_payload,
            }))
    except Exception:
        connection.send(("worker_failure", None))
    finally:
        connection.close()


def execute_news_search(
    settings: NewsExecutionSettings, query: str, *, limit: int, days: int, request_budget_ms: int,
    cursor: str | None = None, worker=news_search_worker,
) -> RawNewsRead:
    maximum = settings.result_limit
    paginated = getattr(settings, "paginated", False)
    if maximum is None or (cursor is not None and not paginated):
        return RawNewsRead(errors=(_error("unsupported"),))
    if not settings.configured():
        return RawNewsRead(errors=(_error("missing_credentials"),))
    seconds = max(0, min(request_budget_ms, 30_000)) / 1000
    deadline = time.monotonic() + max(0, seconds - 2.0)
    if time.monotonic() >= deadline:
        return RawNewsRead(errors=(_error("timeout"),))
    if not _NEWS_WORKER_SLOTS.acquire(blocking=False):
        return RawNewsRead(errors=(NewsError(code="rate_limited", message="新闻执行并发达到上限"),))
    max_results = min(limit if paginated else limit + 1, maximum)
    worker_query = (query, cursor or "first") if paginated else query
    parent = child = process = None
    attempts, codes, payload = 0, set(), None
    started = False
    try:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=worker, args=(child, settings, worker_query, max_results, days, deadline),
                                  name="news-data-fetch", daemon=True)
        process.start()
        started = True
        child.close()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                codes.add("timeout")
                break
            if not parent.poll(min(remaining, 0.1)):
                if process.is_alive():
                    continue
                codes.add("upstream_failure" if attempts else "unsupported")
                break
            try:
                kind, value = parent.recv()
            except EOFError:
                codes.add("upstream_failure" if attempts else "unsupported")
                break
            if kind == "attempt":
                attempts += 1
            elif kind in {"timeout", "configuration_failure", "upstream_failure", "rate_limited"}:
                codes.add(kind)
            elif kind == "http_status":
                if value == 429:
                    codes.add("rate_limited")
                elif value >= 400:
                    codes.add("upstream_failure")
            elif kind == "result":
                payload = value
                break
            elif kind == "worker_failure":
                codes.add("upstream_failure" if attempts else "unsupported")
                break
    except Exception:
        codes.add("upstream_failure" if attempts else "unsupported")
    finally:
        if child is not None:
            child.close()
        if started:
            if process.is_alive():
                process.terminate()
                process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
            else:
                process.join(1)
            if process.is_alive():
                raise RuntimeError("新闻取数执行单元未结束")
        if parent is not None:
            try:
                while parent.poll():
                    kind, value = parent.recv()
                    if kind == "attempt":
                        attempts += 1
                    elif kind in {"timeout", "configuration_failure", "upstream_failure", "rate_limited"}:
                        codes.add(kind)
                    elif kind == "http_status" and value >= 400:
                        codes.add("rate_limited" if value == 429 else "upstream_failure")
            except (EOFError, OSError):
                pass
            finally:
                parent.close()
        if process is not None:
            process.close()
        _NEWS_WORKER_SLOTS.release()
    successful = bool(payload and payload["success"] and attempts)
    if payload and payload["partial"]:
        codes.add("upstream_failure")
    if not successful and not codes:
        codes.add("upstream_failure" if attempts else "unsupported")
    return RawNewsRead(
        records=payload["records"] if successful else (), success=successful, upstream_attempts=attempts,
        errors=tuple(_error(code) for code in sorted(codes)),
        truncated=bool(payload and payload["truncated"]),
        next_cursor=payload.get("next_cursor") if successful and paginated else None,
        examined_count=payload["examined_count"] if payload else None,
        missing_reasons=tuple(payload.get("missing_reasons", ())) if payload else (),
    )
