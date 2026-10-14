"""BaoStock 状态候选：只读所选路由，查询缓存不改变用户主备配置。"""

from datetime import date, datetime, timezone
from importlib.metadata import version
import json
import logging
import re
import subprocess
import sys
from typing import Any, Mapping

from src.services.thesis_ledger_baostock_daily_status import FIELDS, NORMALIZER_REVISION
from src.services.thesis_ledger_status_cache import HistoricalStatusCache
from src.services.thesis_ledger_baostock_lifecycle import LIFECYCLE_REVISION, attach_basic_lifecycle
from src.services.thesis_ledger_trading_status import ROUTE_KEY, TradingStatusSourceError

logger = logging.getLogger(__name__)
TARGET = {"providerId": "baostock", "upstreamSource": "baostock"}


def live_daily_status(symbol: str, start: date, end: date) -> dict[str, Any]:
    """SDK 使用进程级全局连接；独立读取进程隔离并发并限制总耗时。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", __name__, "--read"],
            input=json.dumps({"symbol": symbol, "start": start.isoformat(), "end": end.isoformat()}),
            text=True, capture_output=True, timeout=45, check=True,
        )
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise TradingStatusSourceError("timeout", "BaoStock 日级状态读取超时") from exc
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise TradingStatusSourceError("upstream_failure", "BaoStock 日级状态查询失败") from exc


def read_baostock_status_response(adapter: Any, request: Mapping[str, Any], *,
                                 clock=lambda: datetime.now(timezone.utc), reader=None) -> dict[str, Any]:
    target = request.get("routeTarget", {})
    if request.get("routeKey") != ROUTE_KEY or {key: target.get(key) for key in TARGET} != TARGET:
        raise TradingStatusSourceError("unsupported_capability", "未选择 BaoStock 股票状态路由")
    if (request.get("contractVersion") != 3 or request.get("consumer") != "thesis-ledger"
            or not request.get("requestId") or type(target.get("routeIndex")) is not int
            or target["routeIndex"] not in (0, 1)):
        raise TradingStatusSourceError("invalid_request", "BaoStock 状态请求合同不匹配")
    symbol = request.get("symbol", "")
    if not isinstance(symbol, str) or not re.fullmatch(r"(?:(?:60|68)\d{4}\.SH|(?:00|30)\d{4}\.SZ)", symbol):
        raise TradingStatusSourceError("unsupported_capability", "仅支持明确沪深股票，不支持 ETF 或北交所")
    try:
        window = request["range"]
        start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
        if start > end or (end - start).days > 3660 or end > clock().date():
            raise ValueError("invalid range")
    except (KeyError, TypeError, ValueError) as exc:
        raise TradingStatusSourceError("invalid_request", "BaoStock 状态日期区间非法") from exc
    revision = f"baostock:{version('baostock')}:{NORMALIZER_REVISION}"
    identity = {"symbol": symbol, "start": start.isoformat(), "end": end.isoformat(),
                "capability": ROUTE_KEY["capability"], **TARGET, "revision": revision, "fields": FIELDS, "lifecycleRevision": LIFECYCLE_REVISION}
    cache = getattr(adapter, "_historical_status_cache", None)
    if cache is None:
        cache = HistoricalStatusCache(clock=clock)
        adapter._historical_status_cache = cache
    value = cache.get(identity)
    if value is not None and value.get("basicLifecycle", {}).get("ipoDateStatus") == "unavailable":
        value = None
    if value is None:
        value = (reader or live_daily_status)(symbol, start, end)
        try:
            if value.get("basicLifecycle", {}).get("ipoDateStatus") != "unavailable":
                cache.put(identity, value)
        except OSError:
            logger.warning("日级状态缓存写入失败；本次实际读取仍可消费")
    record_id = f"baostock-status-{value['responseSha256']}"
    days = []
    for item in value["days"]:
        day = {"date": item["date"], "state": item["state"]}
        if item["isST"] in {"0", "1"}:
            day["coarseST"] = "flagged" if item["isST"] == "1" else "notFlagged"
        if item["state"] != "unknown" or "coarseST" in day:
            day["sourceRecordIds"] = [record_id]
        days.append(day)
    coverage = {**value["coverage"], "expectedSessions": value["expectedSessions"]}
    return attach_basic_lifecycle({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": request["requestId"],
        "routeKey": dict(ROUTE_KEY), "routeTarget": dict(target), "symbol": symbol,
        "range": {"start": start.isoformat(), "end": end.isoformat()}, "status": "supported",
        "days": days, "sourceDocuments": [], "structuredCoverage": coverage,
        "sourceRecords": [{"id": record_id, "kind": "normalizedQueryResponse", **TARGET,
                           "symbol": symbol, "range": {"start": start.isoformat(), "end": end.isoformat()},
                           "queryMethod": "query_history_k_data_plus+query_trade_dates", "queryFields": FIELDS,
                           "normalizerRevision": NORMALIZER_REVISION, "responseSha256": value["responseSha256"],
                           "observedAt": value["observedAt"]}],
        "provenance": {**TARGET, "providerRevision": revision, "datasetId": record_id,
                       "fetchedAt": clock().isoformat(), "granularity": "1d", "historicalAvailability": "notVerified"},
    }, value)


if __name__ == "__main__":
    from contextlib import redirect_stdout
    from data_provider.baostock_fetcher import BaostockFetcher
    from src.services.thesis_ledger_baostock_lifecycle import read_status_with_lifecycle

    payload = json.load(sys.stdin)
    with redirect_stdout(sys.stderr):
        result = read_status_with_lifecycle(BaostockFetcher(), symbol=payload["symbol"],
                                          start=date.fromisoformat(payload["start"]), end=date.fromisoformat(payload["end"]))
    print(json.dumps(result, separators=(",", ":")))
