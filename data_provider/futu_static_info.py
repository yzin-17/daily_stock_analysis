"""富途当前证券资料投影；仅提供当前观察值，不推断历史状态。"""

from datetime import date, datetime, timezone
from numbers import Integral
import pandas as pd

from .futu_market import FutuMarketError, futu_security, _unique_row
from .security_lot_size import positive_lot, LotSizeError

CAPABILITY = "SECURITY_STATIC_INFO"


def static_identity(symbol, asset_type):
    code, market, _ = futu_security(symbol)
    canonical = code[3:] + "." + market
    if symbol != canonical or not isinstance(asset_type, str) or asset_type not in {"STOCK", "ETF"}:
        raise FutuMarketError("invalid_request", "静态资料需要规范港美代码和明确的股票或 ETF 类型")
    return code, market


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip()) or bool(pd.isna(value))


def _text(value):
    if _missing(value):
        return None
    if not isinstance(value, str):
        raise FutuMarketError("invalid_response", "静态资料文本字段无效")
    return value.strip()


def read_futu_static_info(sdk, host, port, symbol, asset_type):
    code, market = static_identity(symbol, asset_type)
    context = None
    try:
        context = sdk.OpenQuoteContext(host=host, port=port)
        row = _unique_row(*context.get_stock_basicinfo(
            getattr(sdk.Market, market), stock_type=getattr(sdk.SecurityType, asset_type), code_list=[code],
        ), sdk, code)
        if row.get("stock_type") != asset_type:
            raise FutuMarketError("invalid_response", "静态资料证券类型与请求不一致")
        stock_id = row.get("stock_id")
        if not _missing(stock_id):
            if isinstance(stock_id, bool) or not isinstance(stock_id, Integral) or stock_id <= 0:
                raise FutuMarketError("invalid_response", "证券 ID 必须为精确的正整数")
            stock_id = str(stock_id)
        else:
            stock_id = None
        lot = row.get("lot_size")
        try:
            lot = None if _missing(lot) else positive_lot(lot)
        except LotSizeError:
            raise FutuMarketError("invalid_response", "静态资料每手股数无效") from None
        listing = _text(row.get("listing_date"))
        if listing is not None:
            try:
                if date.fromisoformat(listing).isoformat() != listing:
                    raise ValueError()
            except ValueError:
                raise FutuMarketError("invalid_response", "静态资料上市日期格式无效") from None
        delisted = row.get("delisting")
        if _missing(delisted):
            delisted = None
        else:
            if hasattr(delisted, "item"):
                delisted = delisted.item()
            if not isinstance(delisted, bool):
                raise FutuMarketError("invalid_response", "静态资料退市标志无效")
        return {
            "symbol": symbol, "market": market, "assetType": asset_type,
            "name": _text(row.get("name")), "securityId": stock_id, "sharesPerLot": lot,
            "listingDate": listing, "delisted": delisted, "exchange": _text(row.get("exchange_type")),
            "provider": "futu", "observedAt": datetime.now(timezone.utc).isoformat(),
            "historicalChanges": "unverified",
        }
    finally:
        if context is not None:
            context.close()


def static_info_operation(request, error_type):
    try:
        static_identity(request.symbol, request.instrument_type)
    except FutuMarketError as error:
        raise error_type(error.code, str(error)) from None
    if request.parameters or any(value is not None for value in (
        request.start, request.end, request.timeframe, request.adjustment,
    )):
        raise error_type("invalid_request", "静态资料不接受历史、复权或额外查询参数")

    def operation(provider, adapter, source):
        if (provider, source) != ("futu", "futu"):
            raise error_type("unsupported_source", "静态资料未匹配已接入来源")
        try:
            return adapter.get_security_static_info(request.symbol, request.instrument_type)
        except FutuMarketError as error:
            raise error_type(error.code, str(error), retryable=error.code in {"timeout", "upstream_failure"}) from None
    return operation
