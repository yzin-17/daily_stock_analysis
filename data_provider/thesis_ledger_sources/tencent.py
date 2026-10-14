"""腾讯来源：精确组合、工厂、协议与单目标调用。"""

from datetime import date, datetime
from hashlib import sha256
import json
import re
from typing import Mapping
from types import SimpleNamespace

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, provider_symbol_for_contract
from src.services.thesis_ledger_capability_registry import DailyBinding, ExactKey, QuoteBinding, SourceDefinition
from data_provider.tencent_native_daily import MAX_ROWS, MAX_PARTITIONS, TENCENT_NATIVE_DAILY_PROTOCOL


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("tencent", "腾讯财经", {
        "DAILY_BAR": ("STOCK", "ETF"), "REALTIME_QUOTE": ("ETF",),
    }, upstream_sources=(("tencent", "腾讯财经"),))


def factory(context):
    from data_provider.tencent_fetcher import TencentFetcher
    return TencentFetcher()


def research_calendar_eligible(context):
    from src.services.thesis_ledger_dependency_facts import DependencyFactError, validate_cn_stock
    try:
        validate_cn_stock(context.symbol, context.market, context.asset_type, lambda symbol: symbol)
    except DependencyFactError:
        return False
    return True


def daily(query, context):
    frame = context.adapter.get_daily_data_for_source(
        provider_symbol_for_contract(query.symbol), "tencent", start_date=query.start, end_date=query.end,
        days=(date.fromisoformat(query.end) - date.fromisoformat(query.start)).days + 1,
        adjustment=query.adjustment, timeout_seconds=context.timeout_seconds, asset_type=query.instrument_type,
    )
    if isinstance(frame, tuple) and len(frame) == 2:
        frame = frame[0]
    retrieval = getattr(frame, "attrs", {}).get("tencentDailyRetrieval")
    partitions = retrieval.get("partitions") if isinstance(retrieval, dict) else None
    if not isinstance(partitions, list) or not partitions:
        raise CapabilityContractError("invalid_response", "腾讯日线缺少逐年传输证据")
    return DailyRead(frame, len(partitions))


def quote(query, context):
    value = context.adapter.get_realtime_quote_single_symbol(query.symbol)
    return SimpleNamespace(**value) if isinstance(value, dict) else value


def verify_transport(frame, start, end, adjustment):
    retrieval = frame.attrs.get("tencentDailyRetrieval")
    if not isinstance(retrieval, Mapping) or (
        retrieval.get("endpoint") != "newfqkline/get"
        or retrieval.get("adjustment") != adjustment
        or retrieval.get("partitionComplete") is not True
    ):
        raise CapabilityContractError("invalid_response", "腾讯逐年传输契约无效")
    partitions = retrieval.get("partitions")
    if not isinstance(partitions, list) or not 1 <= len(partitions) <= MAX_PARTITIONS:
        raise CapabilityContractError("invalid_response", "腾讯逐年传输分段数无效")
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
            if (part["start"] != begin.isoformat() or part["end"] != stop.isoformat()
                    or type(part["rows"]) is not int or part["rows"] != count
                    or not isinstance(part["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", part["sha256"])):
                raise ValueError()
        revision = sha256(json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if retrieval.get("revision") != revision:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise CapabilityContractError("invalid_response", "腾讯逐年传输证据无效") from None
    return len(partitions)


SOURCE = SourceDefinition("tencent", manifest, factory, (
    *(DailyBinding(ExactKey("CN", asset, "DAILY_BAR", adjustment), "tencent",
        f"dsa-v3-{asset.lower()}-tencent-{adjustment}-adapter-v2" if adjustment != "hfq"
        else "dsa-v3-etf-tencent-hfq-newfqkline-adapter-v1",
        TENCENT_NATIVE_DAILY_PROTOCOL, TENCENT_NATIVE_DAILY_PROTOCOL, MAX_ROWS, daily, verify_transport=verify_transport,
        research_calendar_eligible=research_calendar_eligible if adjustment in {'none', 'qfq'} else None)
      for asset, adjustment in (("STOCK", "none"), ("STOCK", "qfq"), ("ETF", "none"), ("ETF", "qfq"), ("ETF", "hfq"))),
    QuoteBinding(ExactKey("CN", "ETF", "REALTIME_QUOTE"), "tencent",
                 "tencent-etf-basic-quote-v1", "tencent-etf-single-symbol-v1", quote),
), ("data_provider.tencent_fetcher", "TencentFetcher"), dependency_modules=("requests",),
    quote_source_aliases=(('tencent', 'tencent'), ('akshare_qq', 'tencent')))
