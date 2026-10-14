"""Tushare ETF 来源分段/头部证明；不把完整传输当交易日覆盖。"""

from datetime import date, timedelta
from hashlib import sha256
import json
import re
from typing import Mapping

from .tushare_fund_daily import PAGINATION_PROTOCOL
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def verify_transport(frame, start, end, adjustment=None):
    if adjustment not in {None, "none"}:
        raise CapabilityContractError("invalid_response", "基金日线复权与精确绑定不匹配")
    retrieval = frame.attrs.get("fundDailyRetrieval")
    if not isinstance(retrieval, Mapping) or (
        retrieval.get("endpoint") != "fund_daily" or retrieval.get("adjustment") != "none"
        or retrieval.get("partitionComplete") is not True
        or retrieval.get("tradingCalendarVerified") is not False
        or retrieval.get("nativeVolumeUnit") != "lot-100-units"
        or retrieval.get("nativeAmountUnit") != "CNY-1000"
    ):
        raise CapabilityContractError("invalid_response", "基金日线分段证明无效")
    partitions = retrieval.get("partitions")
    if not isinstance(partitions, list) or not 1 <= len(partitions) <= 32:
        raise CapabilityContractError("invalid_response", "基金日线分段证明无效")
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
        raise CapabilityContractError("invalid_response", "基金日线分段证明无效") from None
    header = frame.attrs.get("thesis_ledger_v3_pagination")
    if not isinstance(header, Mapping) or set(header) != {
        "status", "pagesFetched", "continuationPending", "protocol", "maximumRows", "requestedStart", "requestedEnd",
    } or (
        header.get("status") != "complete" or type(header.get("pagesFetched")) is not int
        or header["pagesFetched"] != len(partitions) or header.get("continuationPending") is not False
        or header.get("protocol") != PAGINATION_PROTOCOL or header.get("maximumRows") is not None
        or header.get("requestedStart") != start or header.get("requestedEnd") != end
    ):
        raise CapabilityContractError("invalid_response", "基金日线完整头部无效")
    return len(partitions)

