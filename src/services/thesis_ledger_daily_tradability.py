"""场内日级可交易性证据分类；不从缺 Bar 推断已证实停牌。"""

from datetime import date, datetime
import math
import re
from typing import Any, Mapping

from src.services.thesis_ledger_listing_information import daily_listing_date


DAILY_TRADABILITY_CONTRACT = "market-daily-tradability-window-v1"


def daily_tradability_evidence(
    *, bars: Any, symbol: str, instrument_type: str, start: str, end: str,
    data_as_of: datetime, route_key: Mapping[str, Any], route_target: Mapping[str, Any],
    provider_revision: str,
    allow_price_only: bool = False,
) -> dict[str, Any]:
    if type(allow_price_only) is not bool:
        raise ValueError("日级观察版本无效")
    metadata = getattr(bars, "attrs", {}).get("daily_tradability_input")
    if not isinstance(metadata, dict) or metadata.get("symbol") != symbol or metadata.get("range") != {"start": start, "end": end}:
        raise ValueError("日级来源证据缺失或范围不符")
    listing = metadata["listing"]
    listed_on = daily_listing_date(listing)
    calendar = metadata["calendar"]
    expected = calendar["expectedSessions"]
    observed_at = metadata["observedAt"]
    for day in (start, end, *([] if listed_on is None else [listed_on]), *expected):
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("日级证据日期必须为规范日期")
    market = route_key.get("market")
    if market not in {"CN", "HK", "US"} or instrument_type not in {"STOCK", "ETF"} or route_key != {
        "kind": "bar", "market": market, "assetType": instrument_type,
        "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": route_key.get("adjustment"),
    } or route_key.get("adjustment") not in {"none", "qfq", "hfq"}:
        raise ValueError("日级来源路由不匹配")
    if any(not isinstance(route_target.get(key), str) or not route_target[key].strip()
           for key in ("providerId", "upstreamSource")):
        raise ValueError("日级来源目标不完整")
    if (
        not provider_revision or provider_revision == "unknown"
        or not re.fullmatch(r"[a-f0-9]{64}", metadata["responseSha256"])
        or metadata.get("paginationComplete") is not True
        or (listed_on is not None and start < listed_on) or start > end
        or not expected or expected != sorted(set(expected))
        or any(day < start or day > end for day in expected)
    ):
        raise ValueError("日级来源证据不完整")
    sources = [calendar["source"]]
    if listed_on is not None:
        sources.append(listing["source"])
    for timestamp in (observed_at, *(source["availableAt"] for source in sources)):
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed > data_as_of:
            raise ValueError("日级证据晚于冻结时点或缺少时区")
    for source in sources:
        if not source.get("provider") or not source.get("revision"):
            raise ValueError("日历或上市来源不可审计")
    by_day = {}
    for _, row in bars.iterrows():
        day = str(row["date"])
        if day not in expected or day in by_day:
            raise ValueError("Bar 日期重复或与日历不符")
        values = {name: float(row[name]) for name in ("open", "high", "low", "close")}
        raw_volume = row.get("volume")
        volume = None if raw_volume is None or math.isnan(float(raw_volume)) else float(raw_volume)
        if volume is not None:
            values["volume"] = volume
        if row.get("amount") is not None and not math.isnan(float(row["amount"])):
            values["amount"] = float(row["amount"])
        if (any(not math.isfinite(value) or value < 0 for value in values.values())
                or (volume is None and not allow_price_only)
                or (volume is not None and volume <= 0)):
            raise ValueError("Bar 量价非法或成交量为零")
        if min(values[name] for name in ("open", "high", "low", "close")) <= 0 or values["high"] < max(values["open"], values["low"], values["close"]) or values["low"] > min(values["open"], values["high"], values["close"]):
            raise ValueError("Bar OHLC 非法")
        by_day[day] = "observed-price-only" if volume is None else "observed-traded"
    missing = [day for day in expected if day not in by_day]
    excluded = metadata.get("excludedZeroVolumeObservations", {})
    if any(day not in missing for day in excluded):
        raise ValueError("零量观察必须对应缺少可执行Bar的已核验交易日")
    if metadata.get("missingSessions") != missing:
        raise ValueError("缺失日期与实际 Bar 不一致")
    return {
        "contract": DAILY_TRADABILITY_CONTRACT, "priceOnlyAllowed": allow_price_only, "symbol": symbol, "market": market, "instrumentType": instrument_type,
        "range": {"start": start, "end": end}, "listing": listing, "calendar": calendar,
        "barSource": {
            "routeKey": dict(route_key),
            "routeTarget": {key: route_target[key] for key in ("providerId", "upstreamSource")},
            "providerRevision": provider_revision, "responseSha256": metadata["responseSha256"],
            "observedAt": observed_at, "requestComplete": True, "paginationComplete": True,
        },
        "days": [{"date": day, "state": by_day.get(day, "assumed-untradable-no-bar"),
                  **({"excludedZeroVolumeObservation": excluded[day]} if day in excluded else {})}
                 for day in expected],
    }
