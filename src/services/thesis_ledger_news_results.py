"""新闻来源记录适配：时间未知、引用、转载和不完整性均显式保留。"""

from hashlib import sha256

from pydantic import ValidationError

from src.services.thesis_ledger_news_contract_v1 import (
    NewsCoverage, NewsError, NewsItem, NewsRequest, NewsResponse, NewsRoute,
)
from src.services.thesis_ledger_news_identity import news_evidence_group, news_item_id, news_time, news_url
from src.services.thesis_ledger_news_records import RawNewsRead, RawNewsRecord


def _known_time(value: str | None, fetched_at: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = news_time(value)
        if parsed <= news_time(fetched_at):
            return parsed.isoformat()
    except (ValueError, TypeError):
        pass
    return None


def _item(record, related_instruments, route, fetched_at, item_type):
    body = " ".join((record.excerpt or record.title).split()).casefold()
    fingerprint = sha256(body.encode()).hexdigest()
    return item_type(
        id=news_item_id(route.providerId, record.source_item_id, record.url),
        title=record.title.strip(), url=record.url, originalUrl=record.original_url,
        publisher=record.publisher, providerId=route.providerId, sourceItemId=record.source_item_id,
        contentFingerprint=fingerprint, evidenceGroupId=news_evidence_group(record.original_url, fingerprint),
        excerpt=record.excerpt, publishedAt=_known_time(record.published_at, fetched_at),
        sourceAvailableAt=_known_time(record.source_available_at, fetched_at), fetchedAt=fetched_at,
        relatedInstruments=related_instruments, sourceUrls=list(dict.fromkeys(
            value for value in (record.url, record.original_url) if value is not None
        )),
    )


def _collapse_reposts(items: list[NewsItem]) -> tuple[list[NewsItem], list[str]]:
    groups: list[tuple[set[str], list[NewsItem]]] = []
    for item in items:
        keys = {"url:" + news_url(item.url), "content:" + item.contentFingerprint}
        if item.originalUrl:
            keys.add("url:" + news_url(item.originalUrl))
        merged = [item]
        remaining = []
        for group_keys, group_items in groups:
            if keys.intersection(group_keys):
                keys.update(group_keys)
                merged.extend(group_items)
            else:
                remaining.append((group_keys, group_items))
        groups = [*remaining, (keys, merged)]
    result = []
    reasons = []
    for _, values in groups:
        contents_by_url: dict[str, set[str]] = {}
        for item in values:
            contents_by_url.setdefault(news_url(item.url), set()).add(item.contentFingerprint)
        if any(len(contents) > 1 for contents in contents_by_url.values()):
            reasons.append("conflicting_source_records")
        primary = min(values, key=lambda item: (item.originalUrl is None, news_url(item.url), item.id))
        urls = sorted({url for item in values for url in item.sourceUrls})
        if len(urls) > 100:
            required = [primary.url]
            if primary.originalUrl:
                required.append(primary.originalUrl)
            urls = list(dict.fromkeys([*required, *urls]))[:100]
            reasons.append("source_urls_truncated")
        result.append(type(primary).model_validate({**primary.model_dump(), "sourceUrls": urls}))
    return sorted(result, key=lambda item: (item.publishedAt or "", item.id), reverse=True), reasons


def build_news_content(request, route, read, *, fetched_at, related_instruments,
                       item_type=NewsItem, coverage_type=NewsCoverage, secrets=()):
    news_time(fetched_at)
    errors = [error.model_copy(update={"message": "新闻来源请求失败"})
              if any(secret and secret in error.message for secret in secrets) else error for error in read.errors]
    reasons = list(read.missing_reasons)
    if request.publishedEnd and news_time(request.publishedEnd) > news_time(fetched_at):
        reasons.append("future_window_not_observed")
    if request.publishedStart and news_time(request.publishedStart) > news_time(fetched_at):
        reasons.append("future_window_not_observed")
    items = []
    if read.success and read.upstream_attempts > 0 and route is not None:
        for record in read.records:
            fields = (record.title, record.url, record.excerpt, record.publisher, record.source_item_id,
                      record.original_url, record.published_at, record.source_available_at)
            if any(secret and any(isinstance(value, str) and secret in value for value in fields) for secret in secrets):
                reasons.append("private_source_value_rejected")
                continue
            try:
                item = _item(record, related_instruments, route, fetched_at, item_type)
            except (ValidationError, ValueError, TypeError, AttributeError):
                reasons.append("invalid_news_record")
                continue
            if item.publishedAt is not None:
                published = news_time(item.publishedAt)
                if ((request.publishedStart and published < news_time(request.publishedStart))
                        or (request.publishedEnd and published > news_time(request.publishedEnd))):
                    continue
            items.append(item)
        items, grouping_reasons = _collapse_reposts(items)
        reasons.extend(grouping_reasons)
    else:
        if not errors:
            errors.append(NewsError(code="upstream_failure", message="新闻来源未完成可核验的检索"))
    truncated = read.truncated or read.next_cursor is not None or len(items) > request.limit
    items = items[:request.limit]
    unknown = sum(item.publishedAt is None for item in items)
    if unknown:
        reasons.append("published_time_unknown")
    if truncated:
        reasons.append("pagination_truncated")
    reasons = sorted(set(reasons))
    successful = read.success and read.upstream_attempts > 0 and route is not None
    complete = successful and not (errors or reasons or truncated)
    if not successful:
        status = "unavailable"
    elif complete:
        status = "available"
    else:
        status = "partial"
    return dict(
        status=status, items=items,
        fetchedAt=fetched_at, route=route, upstreamAttempts=read.upstream_attempts, nextCursor=read.next_cursor,
        coverage=coverage_type(
            publishedStart=request.publishedStart, publishedEnd=request.publishedEnd, complete=complete,
            truncated=truncated, unknownPublishedTimeCount=unknown, examinedCount=read.examined_count,
            returnedCount=len(items), uniqueEvidenceCount=len({item.evidenceGroupId for item in items}),
            missingReasons=reasons,
        ), errors=errors,
    )


def build_news_response(
    request: NewsRequest, route: NewsRoute | None, read: RawNewsRead, *, fetched_at: str,
    secrets: tuple[str, ...] = (),
) -> NewsResponse:
    return NewsResponse(requestId=request.requestId, instrument=request.instrument,
                        **build_news_content(request, route, read, fetched_at=fetched_at,
                                             related_instruments=[request.instrument], secrets=secrets))
