"""富途历史日线：显式复权、完整分页，失败不返回前缀数据。"""

from datetime import date
import pandas as pd

from .exact_daily_contract import ExactDailyError, normalize_exact_daily
from .futu_market import futu_security


def read_futu_daily(sdk, host, port, request):
    code, market, zone = futu_security(request["symbol"])
    asset, adjustment = request["instrumentType"], request["adjustment"]
    if asset not in {"STOCK", "ETF"} or adjustment not in {"none", "qfq", "hfq"}:
        raise ExactDailyError("invalid_request", "富途日线的资产类型或复权参数无效")
    try:
        start, end = date.fromisoformat(request["start"]), date.fromisoformat(request["end"])
        if start > end:
            raise ValueError("inverted window")
    except (TypeError, ValueError):
        raise ExactDailyError("invalid_request", "富途日线需要有效的起止日期") from None
    context = None
    try:
        context = sdk.OpenQuoteContext(host=host, port=port)
        status, static = context.get_stock_basicinfo(getattr(sdk.Market, market),
            stock_type=getattr(sdk.SecurityType, asset), code_list=[code])
        if status != sdk.RET_OK:
            raise ExactDailyError("upstream_failure", "富途静态资料读取失败")
        if (not isinstance(static, pd.DataFrame) or len(static) != 1
                or static.iloc[0].get("code") != code or static.iloc[0].get("stock_type") != asset):
            raise ExactDailyError("invalid_response", "富途日线证券或资产类型不匹配")
        autype = {"none": sdk.AuType.NONE, "qfq": sdk.AuType.QFQ, "hfq": sdk.AuType.HFQ}[adjustment]
        cursor, seen, pages = None, set(), []
        for _ in range(64):
            status, frame, next_cursor = context.request_history_kline(
                code, start=start.isoformat(), end=end.isoformat(), ktype=sdk.KLType.K_DAY,
                autype=autype, max_count=1000, page_req_key=cursor, extended_time=False,
            )
            if status != sdk.RET_OK:
                raise ExactDailyError("upstream_failure", "富途历史日线读取失败，请检查权限与历史额度")
            if (not isinstance(frame, pd.DataFrame) or frame.empty or "code" not in frame
                    or not frame["code"].eq(code).all() or "time_key" not in frame):
                raise ExactDailyError("invalid_response", "富途日线分页缺失或证券身份无效")
            try:
                days = frame["time_key"].map(lambda value: date.fromisoformat(str(value)[:10]))
                if any(day < start or day > end for day in days):
                    raise ValueError("out of window")
            except (TypeError, ValueError):
                raise ExactDailyError("invalid_response", "富途日线分页日期无效或超出窗口") from None
            pages.append(frame)
            if next_cursor is None:
                raw = pd.concat(pages, ignore_index=True)
                return normalize_exact_daily(raw, start=start.isoformat(), end=end.isoformat(), source="futu",
                    columns={"time_key": "date", "turnover": "amount"}, timezone=zone)
            if not isinstance(next_cursor, bytes) or not next_cursor or next_cursor in seen:
                raise ExactDailyError("invalid_response", "富途历史日线分页游标无效或循环")
            seen.add(next_cursor)
            cursor = next_cursor
        raise ExactDailyError("insufficient_coverage", "富途日线超过分页上限，未提交不完整数据")
    finally:
        if context is not None:
            context.close()
