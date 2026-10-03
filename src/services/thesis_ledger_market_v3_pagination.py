"""精确行情读取的传输完整性校验；独立交易日覆盖由调用方核验。"""

from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import re
from typing import Mapping

from data_provider.tencent_native_daily import (
    MAX_PARTITIONS, MAX_ROWS, TENCENT_NATIVE_DAILY_PROTOCOL,
)
from data_provider.tushare_fund_daily import PAGINATION_PROTOCOL
from src.services.thesis_ledger_market_v3_facts import market_pagination_contract_v3


class MarketPaginationError(ValueError):
    def __init__(self, code="invalid_response"):
        self.code = code
        super().__init__(code)


def _tushare_partition_count(frame, start, end):
    retrieval = frame.attrs.get("fundDailyRetrieval")
    if not isinstance(retrieval, Mapping) or (
        retrieval.get("endpoint") != "fund_daily" or retrieval.get("adjustment") != "none"
        or retrieval.get("partitionComplete") is not True
        or retrieval.get("tradingCalendarVerified") is not False
        or retrieval.get("nativeVolumeUnit") != "lot-100-units"
        or retrieval.get("nativeAmountUnit") != "CNY-1000"
    ):
        raise MarketPaginationError()
    partitions = retrieval.get("partitions")
    if not isinstance(partitions, list) or not 1 <= len(partitions) <= 32:
        raise MarketPaginationError()
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first.isoformat() != start or last.isoformat() != end or first > last:
            raise ValueError()
        expected_count = (last - first).days // 366 + 1
        if len(partitions) != expected_count:
            raise ValueError()
        row_count = 0
        for index, part in enumerate(partitions):
            begin = first + timedelta(days=index * 366)
            stop = begin + timedelta(days=min(365, (last - begin).days))
            if not isinstance(part, dict) or set(part) != {"start", "end", "rows", "sha256"}:
                raise ValueError()
            if (
                part["start"] != begin.isoformat() or part["end"] != stop.isoformat()
                or type(part["rows"]) is not int or not 0 <= part["rows"] <= (stop - begin).days + 1
                or not isinstance(part["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", part["sha256"])
            ):
                raise ValueError()
            row_count += part["rows"]
        revision = sha256(json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if row_count != len(frame) or retrieval.get("revision") != revision:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise MarketPaginationError() from None
    return len(partitions)


def _tencent_partition_count(frame, start, end, adjustment):
    retrieval = frame.attrs.get("tencentDailyRetrieval")
    if not isinstance(retrieval, Mapping) or (
        retrieval.get("endpoint") != "newfqkline/get"
        or retrieval.get("adjustment") != adjustment
        or retrieval.get("partitionComplete") is not True
    ):
        raise MarketPaginationError()
    partitions = retrieval.get("partitions")
    if not isinstance(partitions, list) or not 1 <= len(partitions) <= MAX_PARTITIONS:
        raise MarketPaginationError()
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first.isoformat() != start or last.isoformat() != end or first > last:
            raise ValueError()
        dates = []
        for value in frame["date"]:
            if isinstance(value, datetime):
                day = value.date()
            elif isinstance(value, date):
                day = value
            else:
                day = date.fromisoformat(value)
                if day.isoformat() != value:
                    raise ValueError()
            dates.append(day)
        if len(dates) != len(set(dates)) or len(dates) > MAX_ROWS:
            raise ValueError()
        expected = []
        cursor = first
        while cursor <= last:
            stop = min(last, date(cursor.year, 12, 31))
            expected.append((cursor, stop))
            if stop == last:
                break
            cursor = date(stop.year + 1, 1, 1)
        if len(expected) != len(partitions):
            raise ValueError()
        for part, (begin, stop) in zip(partitions, expected):
            if not isinstance(part, dict) or set(part) != {"start", "end", "rows", "sha256"}:
                raise ValueError()
            count = sum(begin <= day <= stop for day in dates)
            if (
                part["start"] != begin.isoformat() or part["end"] != stop.isoformat()
                or type(part["rows"]) is not int or part["rows"] != count
                or not isinstance(part["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", part["sha256"])
            ):
                raise ValueError()
        revision = sha256(json.dumps(
            partitions, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        if retrieval.get("revision") != revision:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise MarketPaginationError() from None
    return len(partitions)


def market_pagination_proof_v3(
    frame, *, asset_type, provider, upstream_source, expected_session_count,
    requested_start, requested_end, requested_adjustment=None,
):
    contract = market_pagination_contract_v3(asset_type, provider, upstream_source)
    attrs = getattr(frame, "attrs", None)
    evidence = attrs.get("thesis_ledger_v3_pagination") if isinstance(attrs, Mapping) else None
    if contract is None or not isinstance(evidence, Mapping) or set(evidence) != {
        "status", "pagesFetched", "continuationPending", "protocol", "maximumRows", "requestedStart", "requestedEnd",
    }:
        raise MarketPaginationError()
    expected_pages = 1
    if contract["protocol"] == PAGINATION_PROTOCOL:
        expected_pages = _tushare_partition_count(frame, requested_start, requested_end)
    elif contract["protocol"] == TENCENT_NATIVE_DAILY_PROTOCOL:
        expected_pages = _tencent_partition_count(
            frame, requested_start, requested_end, requested_adjustment,
        )
    if (
        evidence.get("status") != "complete" or evidence.get("protocol") != contract["protocol"]
        or type(evidence.get("pagesFetched")) is not int or evidence["pagesFetched"] != expected_pages
        or evidence.get("continuationPending") is not False
        or evidence.get("maximumRows") != contract["maximumRows"]
        or evidence.get("requestedStart") != requested_start or evidence.get("requestedEnd") != requested_end
    ):
        raise MarketPaginationError()
    if contract["maximumRows"] is not None and expected_session_count > contract["maximumRows"]:
        raise MarketPaginationError("insufficient_coverage")
    return {"status": "complete", "pagesFetched": expected_pages, "continuationPending": False}
