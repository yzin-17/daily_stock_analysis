"""港股原价与当前整手观察分别保留；每手股数仅供研究模型采用或覆盖。"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily

SOURCE = "yahoo-hk-share-window"
PACK_PATH = Path(__file__).with_name("market_reference") / "hk00147-share-window.json"


def share_window_scope(symbol, start, end, pack):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if symbol != pack["symbol"] or first > last or start < pack["coverage"]["start"] or end > pack["coverage"]["end"]:
        raise ExactDailyError("not_covered", "港股按股来源只覆盖00147的已核对窗口")


def load_share_window(path=PACK_PATH):
    try:
        pack_bytes = path.read_bytes()
        pack = json.loads(pack_bytes)
        if (pack["schemaVersion"] != 1 or pack["symbol"] != "00147.HK" or pack["market"] != "HK"
                or pack["sourceSymbol"] != "0147.HK" or pack["currency"] != "HKD"
                or pack["timezone"] != "Asia/Hong_Kong" or pack["upstreamSource"] != SOURCE
                or pack["rawFile"] != "hk00147-share-window.raw.json"
                or pack["coverage"] != {"start": "2025-08-06", "end": "2025-08-11"}
                or pack["lotReference"]["sharesPerLot"] != "5000"
                or pack["lotReference"]["historicalChanges"] != "unverified"
                or pack["lotReference"]["provider"] != "webull-current-quote"
                or pack["priceGrid"]["minimumTick"] != "0.001"
                or pack["priceGrid"]["representationTolerance"] != "0.00000005"
                or pack["priceGrid"]["effectiveFrom"] != "2025-08-04"
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
                meta["exchangeTimezoneName"]) != ("0147.HK", "HKD", "HKG", "EQUITY", "Asia/Hong_Kong"):
            raise ValueError("identity")
        if result.get("events", {}) != {}:
            raise ValueError("window events")
        clocks = [datetime.fromisoformat(item["observedAt"]) for item in
                  (pack, pack["eventObservation"], pack["lotReference"], pack["priceGrid"])]
        if any(value.tzinfo is None for value in clocks):
            raise ValueError("observation clock")
        return pack, result, max(clocks), "yahoo-hk-share-window-sha256:" + sha256(pack_bytes + raw).hexdigest()
    except (OSError, KeyError, TypeError, ValueError, IndexError) as error:
        raise ExactDailyError("invalid_response", "港股按股窗口或原响应身份/摘要无效") from error


def price_on_share_grid(value):
    try:
        if isinstance(value, bool):
            raise ValueError("boolean price")
        raw = Decimal(str(value))
        if not raw.is_finite() or raw < Decimal("0.01") or raw > Decimal("0.50"):
            raise ValueError("price range")
        tick = Decimal("0.001") if raw <= Decimal("0.25000005") else Decimal("0.005")
        rounded = (raw / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
        if abs(raw - rounded) > Decimal("0.00000005"):
            raise ValueError("price representation")
        return float(rounded)
    except (InvalidOperation, ValueError) as error:
        raise ExactDailyError("invalid_response", "港股价格不符合已核对价位或浮点误差界限") from error


def read_yahoo_hk_share_window(request):
    pack, result, observed, revision = load_share_window()
    share_window_scope(request.symbol, request.start, request.end, pack)
    if request.instrument_type != "STOCK" or request.timeframe != "1d" or request.adjustment != "none":
        raise ExactDailyError("unsupported_adjustment", "港股按股窗口只支持股票日线原价")
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"]["adjclose"][0]["adjclose"]
    times = result["timestamp"]
    if any(len(quote[field]) != len(times) for field in ("open", "high", "low", "close", "volume")) or len(adjusted) != len(times):
        raise ExactDailyError("invalid_response", "港股窗口数组长度不一致")
    rows = []
    for index, timestamp in enumerate(times):
        if quote["close"][index] != adjusted[index] or type(quote["volume"][index]) is not int or quote["volume"][index] < 0:
            raise ExactDailyError("invalid_response", "港股窗口复权或成交量语义变化")
        day = datetime.fromtimestamp(timestamp, ZoneInfo(pack["timezone"])).date().isoformat()
        rows.append({"date": day, **{field: price_on_share_grid(quote[field][index]) for field in ("open", "high", "low", "close")},
                     "volume": quote["volume"][index], "amount": None})
    if len({row["date"] for row in rows}) != len(times):
        raise ExactDailyError("invalid_response", "港股窗口日期重复")
    frame = normalize_exact_daily(pd.DataFrame(rows), start=request.start, end=request.end, source=SOURCE, timezone=pack["timezone"])
    frame.attrs.update({"responseSha256": pack["rawSha256"], "shareWindowRevision": revision,
                        "shareWindowObservedAt": observed.isoformat()})
    return frame


def share_window_instrument_fact(symbol, start, end, data_as_of):
    pack, _, observed, revision = load_share_window()
    share_window_scope(symbol, start, end, pack)
    if data_as_of < observed:
        raise ExactDailyError("not_covered", "港股研究单位观察晚于冻结截点")
    return {"symbol": symbol, "market": "HK", "instrumentType": "STOCK", "currency": "HKD",
            "lotSize": pack["lotReference"]["sharesPerLot"], "tickSize": pack["priceGrid"]["minimumTick"], "tradable": False,
            "executionRules": {"status": "unavailable", "reason": "当前5000股来源只供可覆盖研究预设，未核验历史整手变更；日级状态与其他制度未知"},
            "provider": SOURCE, "providerRevision": revision,
            "occurredAt": observed.isoformat(), "availableAt": observed.isoformat()}
