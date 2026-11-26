"""富途历史 K 线的连接、精确身份与完整分页；不负责日/分钟投影。"""

from datetime import date

import pandas as pd

from .exact_daily_contract import ExactDailyError
from .futu_market import futu_security
from src.services.thesis_ledger_provider_errors import classify_provider_failure


def read_futu_history(sdk, host, port, request, *, timeframe):
    code, market, zone = futu_security(request.get("symbol"))
    asset, adjustment = request.get("instrumentType"), request.get("adjustment")
    adjustments = {"none"} if timeframe != "1d" else {"none", "qfq", "hfq"}
    if timeframe not in {"1d", "1m", "5m", "15m", "30m", "60m"} or asset not in {"STOCK", "ETF"} or adjustment not in adjustments:
        raise ExactDailyError("invalid_request", "富途历史 K 线的周期、资产或复权参数无效")
    try:
        start, end = date.fromisoformat(request["start"]), date.fromisoformat(request["end"])
        if start > end:
            raise ValueError("inverted window")
    except (KeyError, TypeError, ValueError):
        raise ExactDailyError("invalid_request", "富途历史 K 线需要有效的起止日期") from None
    if market in {"SH", "SZ"} and timeframe == "1d" and asset == "STOCK":
        if not code.startswith(("SH.6", "SZ.0", "SZ.3")):
            raise ExactDailyError("invalid_request", "富途股票日线仅接入沪深 A 股")
    if market in {"SH", "SZ"} and timeframe == "1d" and asset == "ETF":
        from src.services.thesis_ledger_dependency_facts import DependencyFactError, validate_cn_stock
        try:
            validate_cn_stock(request['symbol'], 'CN', asset, lambda symbol: symbol)
        except DependencyFactError:
            raise ExactDailyError('invalid_request', '富途 ETF 日线需要沪深 ETF 证券身份') from None
    context = None
    try:
        context = sdk.OpenQuoteContext(host=host, port=port)
        status, static = context.get_stock_basicinfo(getattr(sdk.Market, market),
            stock_type=getattr(sdk.SecurityType, asset), code_list=[code])
        if status != sdk.RET_OK:
            raise ExactDailyError(classify_provider_failure(static), "富途静态资料读取失败")
        if (not isinstance(static, pd.DataFrame) or len(static) != 1
                or static.iloc[0].get("code") != code or static.iloc[0].get("stock_type") != asset):
            raise ExactDailyError("invalid_response", "富途历史 K 线证券或资产类型不匹配")
        autype = {"none": sdk.AuType.NONE, "qfq": sdk.AuType.QFQ, "hfq": sdk.AuType.HFQ}[adjustment]
        ktype = getattr(sdk.KLType, {"1d": "K_DAY", "1m": "K_1M", "5m": "K_5M",
                                   "15m": "K_15M", "30m": "K_30M", "60m": "K_60M"}[timeframe])
        cursor, seen, pages = None, set(), []
        for _ in range(64):
            status, frame, next_cursor = context.request_history_kline(
                code, start=start.isoformat(), end=end.isoformat(), ktype=ktype,
                autype=autype, max_count=1000, page_req_key=cursor, extended_time=False,
            )
            if status != sdk.RET_OK:
                raise ExactDailyError(classify_provider_failure(frame), "富途历史 K 线读取失败，请核对来源状态")
            if (not isinstance(frame, pd.DataFrame) or frame.empty or "code" not in frame
                    or not frame["code"].eq(code).all() or "time_key" not in frame):
                raise ExactDailyError("invalid_response", "富途历史 K 线分页缺失或证券身份无效")
            try:
                days = frame["time_key"].map(lambda value: date.fromisoformat(str(value)[:10]))
                if any(day < start or day > end for day in days):
                    raise ValueError("out of window")
            except (TypeError, ValueError):
                raise ExactDailyError("invalid_response", "富途历史 K 线日期无效或超出窗口") from None
            pages.append(frame)
            if next_cursor is None:
                combined = pd.concat(pages, ignore_index=True)
                if timeframe != "1d":
                    combined.attrs["pagination"] = {
                        "requestedStart": start.isoformat(), "requestedEnd": end.isoformat(),
                        "pagesFetched": len(pages), "continuationPending": False,
                    }
                return combined, zone
            if not isinstance(next_cursor, bytes) or not next_cursor or next_cursor in seen:
                raise ExactDailyError("invalid_response", "富途历史 K 线分页游标无效或循环")
            seen.add(next_cursor)
            cursor = next_cursor
        raise ExactDailyError("insufficient_coverage", "富途历史 K 线超过分页上限，未提交不完整数据")
    finally:
        if context is not None:
            context.close()
