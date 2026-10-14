"""已核对的GME按股价格窗口；不扩展普通Yahoo复权或全市场原价能力。"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily

SOURCE = "yahoo-gme-share-window"
PACK_PATH = Path(__file__).with_name("market_reference") / "us-gme-share-window.json"


def load_share_window(path=PACK_PATH):
    try:
        pack_bytes = path.read_bytes()
        pack = json.loads(pack_bytes)
        if (pack["schemaVersion"] != 1 or pack["symbol"] != "GME.US" or pack["market"] != "US"
                or pack["sourceSymbol"] != "GME" or pack["currency"] != "USD"
                or pack["timezone"] != "America/New_York" or pack["upstreamSource"] != SOURCE
                or pack["rawFile"] != "us-gme-share-window.raw.json"
                or pack["coverage"] != {"start": "2024-05-10", "end": "2024-05-24"}
                or pack["unitModel"]["quantityStep"] != "1" or pack["unitModel"]["tickSize"] != "0.01"
                or pack["unitModel"]["representationTolerance"] != "0.000005"
                or pack["eventObservation"]["events"] != {}):
            raise ValueError("qualified scope")
        raw = path.with_name(pack["rawFile"]).read_bytes().removesuffix(b"\n")
        if len(raw) != pack["rawBytes"] or sha256(raw).hexdigest() != pack["rawSha256"]:
            raise ValueError("raw digest")
        chart = json.loads(raw)["chart"]
        if chart["error"] is not None or len(chart["result"]) != 1:
            raise ValueError("chart")
        result = chart["result"][0]
        meta = result["meta"]
        if (meta["symbol"], meta["currency"], meta["exchangeName"], meta["instrumentType"],
                meta["exchangeTimezoneName"]) != ("GME", "USD", "NYQ", "EQUITY", "America/New_York"):
            raise ValueError("identity")
        if result.get("events", {}) != {}:
            raise ValueError("window events")
        price_observed = datetime.fromisoformat(pack["observedAt"])
        events_observed = datetime.fromisoformat(pack["eventObservation"]["observedAt"])
        unit_observed = datetime.fromisoformat(pack["unitModel"]["observedAt"])
        if any(value.tzinfo is None for value in (price_observed, events_observed, unit_observed)):
            raise ValueError("observation clock")
        observed = max(price_observed, events_observed, unit_observed)
        return pack, result, observed, "yahoo-share-window-sha256:" + sha256(pack_bytes + raw).hexdigest()
    except (OSError, KeyError, TypeError, ValueError, IndexError) as error:
        raise ExactDailyError("invalid_response", "美股按股窗口或原响应身份/摘要无效") from error


def share_window_scope(symbol, start, end, pack):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if symbol != pack["symbol"] or first > last or start < pack["coverage"]["start"] or end > pack["coverage"]["end"]:
        raise ExactDailyError("not_covered", "美股按股来源仅覆盖GME的已核对历史窗口")


def price_on_share_grid(value):
    try:
        if isinstance(value, bool):
            raise ValueError("boolean price")
        raw = Decimal(str(value))
        rounded = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not raw.is_finite() or rounded <= 0 or abs(raw - rounded) > Decimal("0.000005"):
            raise ValueError("price representation")
        return float(rounded)
    except (InvalidOperation, ValueError) as error:
        raise ExactDailyError("invalid_response", "按股价格不符合已核对价位或浮点误差界限") from error


def read_yahoo_share_window(request):
    pack, result, observed, revision = load_share_window()
    share_window_scope(request.symbol, request.start, request.end, pack)
    if request.instrument_type != "STOCK" or request.timeframe != "1d" or request.adjustment != "none":
        raise ExactDailyError("unsupported_adjustment", "按股窗口只支持股票日线原价")
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"]["adjclose"][0]["adjclose"]
    times = result["timestamp"]
    if any(len(quote[field]) != len(times) for field in ("open", "high", "low", "close", "volume")) or len(adjusted) != len(times):
        raise ExactDailyError("invalid_response", "按股窗口数组长度不一致")
    rows = []
    for index, timestamp in enumerate(times):
        if quote["close"][index] != adjusted[index] or type(quote["volume"][index]) is not int or quote["volume"][index] <= 0:
            raise ExactDailyError("invalid_response", "按股窗口复权或成交量语义变化")
        day = datetime.fromtimestamp(timestamp, ZoneInfo(pack["timezone"])).date().isoformat()
        rows.append({"date": day, **{field: price_on_share_grid(quote[field][index]) for field in ("open", "high", "low", "close")},
                     "volume": quote["volume"][index], "amount": None})
    by_date = {row["date"]: row for row in rows}
    if len(by_date) != len(times) or str(by_date[pack["anchor"]["date"]]["close"]) != pack["anchor"]["closePerShare"]:
        raise ExactDailyError("invalid_response", "按股窗口日期或独立每股价格锚不一致")
    frame = normalize_exact_daily(pd.DataFrame(rows), start=request.start, end=request.end, source=SOURCE, timezone=pack["timezone"])
    frame.attrs.update({"responseSha256": pack["rawSha256"], "shareWindowRevision": revision,
                        "shareWindowObservedAt": observed.isoformat()})
    return frame


def share_window_instrument_fact(symbol, start, end, data_as_of):
    pack, _, observed, revision = load_share_window()
    share_window_scope(symbol, start, end, pack)
    if data_as_of < observed:
        raise ExactDailyError("not_covered", "按股资格观察晚于冻结截点")
    return {"symbol": symbol, "market": "US", "instrumentType": "STOCK", "currency": "USD",
            "lotSize": "1", "tickSize": "0.01", "tradable": False,
            "executionRules": {"status": "unavailable", "reason": "仅核对每股价格与整股研究步长，日级状态和其他制度由独立来源/显式模型决定"},
            "provider": SOURCE, "providerRevision": revision,
            "occurredAt": observed.isoformat(), "availableAt": observed.isoformat()}
