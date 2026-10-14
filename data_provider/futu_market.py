"""富途精确单标报价；短查询隔离 SDK 连接并复用每手股数适配。"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

from .futu_lot_size import FutuLotSizeAdapter
from src.services.thesis_ledger_quote_values import optional_quote_number

RESULT_PREFIX = "THESIS_FUTU_MARKET="


class FutuMarketError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def futu_security(symbol):
    """接受产品标的及现有报价分发的港股代码，禁止混淆其他市场。"""
    value = str(symbol or "").strip().upper()
    match = re.fullmatch(r"(?:HK\.?([0-9]{1,5})|([0-9]{1,5})\.HK)", value)
    if match:
        code = (match[1] or match[2]).zfill(5)
        if int(code) > 0:
            return "HK." + code, "HK", "Asia/Hong_Kong"
        raise FutuMarketError("invalid_request", "富途证券代码不能为零")
    if re.match(r"^(HK\.?[0-9]|SH\.|SZ\.|BJ\.)", value):
        raise FutuMarketError("invalid_request", "富途证券代码格式或市场不匹配")
    if value.endswith(".US"):
        value = value[:-3]
    elif value.startswith("US."):
        value = value[3:]
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,31}", value) and not value.endswith((".HK", ".SH", ".SZ", ".BJ", ".OF")):
        return "US." + value, "US", "America/New_York"
    raise FutuMarketError("invalid_request", "富途报价需要明确的港股或美股证券代码")


def _unique_row(status, rows, sdk, code):
    if status != sdk.RET_OK:
        raise FutuMarketError("upstream_failure", "富途读取失败，请检查 OpenD 登录、行情权限与调用额度")
    if not hasattr(rows, "iterrows") or len(rows) != 1:
        raise FutuMarketError("not_covered", "富途未返回唯一的目标证券")
    row = next(rows.iterrows())[1]
    if row.get("code") != code:
        raise FutuMarketError("invalid_response", "富途响应证券与请求不一致")
    return row


def read_futu_quote(sdk, host, port, symbol, instrument_type):
    code, market, zone = futu_security(symbol)
    if instrument_type not in {"STOCK", "ETF"}:
        raise FutuMarketError("unsupported", "当前精确报价仅接入股票和 ETF")
    context = None
    try:
        context = sdk.OpenQuoteContext(host=host, port=port)
        static = _unique_row(*context.get_stock_basicinfo(
            getattr(sdk.Market, market), stock_type=getattr(sdk.SecurityType, instrument_type), code_list=[code],
        ), sdk, code)
        if str(static.get("stock_type")) != instrument_type:
            raise FutuMarketError("invalid_response", "富途证券类型与请求不一致")
        row = _unique_row(*context.get_market_snapshot([code]), sdk, code)
        observed = row.get("update_time")
        provider_time = None
        if observed is not None and str(observed).strip():
            try:
                provider_time = datetime.strptime(str(observed), "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=ZoneInfo(zone),
                ).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                raise FutuMarketError("invalid_response", "富途报价来源时间格式无效") from None
        result = {"source": "futu", "provider_timestamp": provider_time,
                  "fetched_at": datetime.now(timezone.utc).isoformat()}
        for field, original in {"price": "last_price", "pre_close": "prev_close_price",
                                "open_price": "open_price", "high": "high_price", "low": "low_price"}.items():
            value = row.get(original)
            if isinstance(value, bool):
                raise FutuMarketError("invalid_response", "富途价格字段不是有效数值")
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                raise FutuMarketError("invalid_response", "富途价格字段缺失或无效") from None
            if not math.isfinite(value):
                raise FutuMarketError("invalid_response", "富途价格字段不是有限数值")
            result[field] = value
        result.update(volume=optional_quote_number(row.get("volume")), amount=optional_quote_number(row.get("turnover")))
        return result
    finally:
        if context is not None:
            context.close()


class FutuMarketAdapter(FutuLotSizeAdapter):
    """复用 LOT 静态资料缓存；报价无隐式来源或历史数据降级。"""

    def get_realtime_quote(self, symbol, *, strict_source=True, instrument_type="STOCK"):
        futu_security(symbol)
        if strict_source is not True:
            raise FutuMarketError("invalid_request", "富途报价只允许精确来源读取")
        return self._query({"operation": "quote", "symbol": symbol, "instrumentType": instrument_type})

    def get_exact_daily(self, request):
        import pandas as pd
        value = self._query({"operation": "daily", "symbol": request.symbol, "instrumentType": request.instrument_type,
                             "start": request.start, "end": request.end, "adjustment": request.adjustment})
        frame = pd.DataFrame(value["rows"])
        frame.attrs.update(value["attrs"])
        return frame

    def get_security_static_info(self, symbol, asset_type):
        from .futu_static_info import static_identity
        static_identity(symbol, asset_type)
        return self._query({"operation": "static-info", "symbol": symbol, "instrumentType": asset_type})

    def _query(self, request):
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "data_provider.futu_query_worker"],
                input=json.dumps({"host": self.host, "port": self.port, **request}),
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                timeout=4.5, check=False,
            )
            results = [line[len(RESULT_PREFIX):] for line in completed.stdout.splitlines() if line.startswith(RESULT_PREFIX)]
            if completed.returncode != 0 or len(results) != 1:
                raise FutuMarketError("upstream_failure", "富途行情进程未返回有效结果")
            value = json.loads(results[0])
            if not isinstance(value, dict):
                raise ValueError("invalid result")
        except subprocess.TimeoutExpired:
            raise FutuMarketError("timeout", "连接 OpenD 或读取行情超时") from None
        except (OSError, ValueError) as error:
            if isinstance(error, FutuMarketError):
                raise
            raise FutuMarketError("invalid_response", "富途行情进程返回无效数据") from None
        if value.get("error"):
            raise FutuMarketError(value["error"], value["message"])
        return value
