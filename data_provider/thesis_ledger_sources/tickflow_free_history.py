"""TickFlow 免费历史连接；复用完整服务的SDK查询/规范化，认证与版本独立。"""

from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.thesis_ledger_sources.tickflow import read_frame
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, DailyQuery, ReadContext, SourceProbePolicy
from src.services.thesis_ledger_capability_registry import DailyBinding, ExactKey, SourceDefinition

BASE_URL = "https://free-api.tickflow.org"
UPSTREAM = "tickflow-free-history"
PROTOCOL = "dsa-tickflow-free-history-single-response-v1"
MAXIMUM_ROWS = 10000
MAXIMUM_WINDOW_DAYS = 3650


def symbol_in_scope(symbol):
    # 本次CNY研究只支持沪深A股；不把B股/基金/指数或交易所后缀泛化为同一币种。
    return re.fullmatch(r"(?:60|68)[0-9]{4}\.SH|(?:00|30)[0-9]{4}\.SZ", symbol) is not None


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("tickflow-free", "TickFlow 免费历史", {
        "DAILY_BAR": ("STOCK",),
    }, upstream_sources=((UPSTREAM, "TickFlow 免费历史日K"),), markets=("CN",))


class FreeHistoryConnection:
    """仅持有单次免费日线连接，不复用完整服务Key、缓存或报价客户端。"""

    @staticmethod
    def _daily_kline_count(start, end):
        from data_provider.tickflow_fetcher import TickFlowFetcher
        return TickFlowFetcher._daily_kline_count(start, end)

    @staticmethod
    def _date_to_ms(day, *, end_of_day=False):
        from data_provider.tickflow_fetcher import TickFlowFetcher
        return TickFlowFetcher._date_to_ms(day, end_of_day=end_of_day)

    def __init__(self, timeout):
        from tickflow import TickFlow
        # None会让SDK读取环境Key；显式空字符串保证不会发送完整服务凭据。
        self.client = TickFlow(api_key="", base_url=BASE_URL, timeout=timeout, max_retries=0)

    def _get_client(self):
        return self.client

    def close(self):
        self.client.close()


def factory(context):
    return FreeHistoryConnection(context.timeout_seconds / 2)


def research_calendar_eligible(context):
    from src.services.thesis_ledger_dependency_facts import DependencyFactError, validate_cn_stock
    try:
        validate_cn_stock(context.symbol, context.market, context.asset_type, lambda symbol: symbol)
    except DependencyFactError:
        return False
    return symbol_in_scope(context.symbol)


def validate_compact(payload, request, count):
    if not isinstance(payload, dict) or not isinstance(payload.get("timestamp"), list):
        raise ExactDailyError("invalid_response", "免费日K未返回紧凑毫秒时间戳序列")
    size = len(payload["timestamp"])
    if size >= count or size >= MAXIMUM_ROWS:
        raise ExactDailyError("insufficient_coverage", "免费日K达到单响应行数预算，不能证明整窗已取得")
    for field in ("open", "high", "low", "close"):
        if not isinstance(payload.get(field), list) or len(payload[field]) != size:
            raise ExactDailyError("invalid_response", "免费日K价格列缺失或长度冲突")
    for field in ("volume", "amount"):
        if field in payload and (not isinstance(payload[field], list) or len(payload[field]) != size):
            raise ExactDailyError("invalid_response", "免费日K量额列长度冲突")
    if "symbol" in payload and payload["symbol"] != request.symbol:
        raise ExactDailyError("invalid_response", "免费日K证券回显与请求不一致")


def daily(query, context):
    try:
        if query.instrument_type != "STOCK" or not symbol_in_scope(query.symbol):
            raise ExactDailyError("invalid_request", "免费历史当前仅支持沪深A股完整证券代码")
        if (date.fromisoformat(query.end) - date.fromisoformat(query.start)).days + 1 > MAXIMUM_WINDOW_DAYS:
            raise ExactDailyError("invalid_request", "免费历史窗口超过3650日的有界读取预算")
        instrument = context.adapter._get_client().instruments.get(query.symbol)
        if (not isinstance(instrument, dict) or instrument.get("symbol") != query.symbol
                or instrument.get("exchange") != query.symbol.rsplit(".", 1)[1]
                or instrument.get("region") != "CN" or instrument.get("type") != "stock"):
            raise ExactDailyError("invalid_response", "免费标的元数据未确认所请求沪深股票身份")
        frame = read_frame(context.adapter, query, source=UPSTREAM, validate_raw=validate_compact)
        frame.attrs["tickflow_free_history_identity"] = {
            "basis": "current-provider-instrument-metadata", "instrument": instrument,
            "klineIdentity": "request-bound", "historicalIdentity": "notVerified",
        }
        return DailyRead(frame, 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None
    finally:
        context.adapter.close()


def probe_daily(context):
    symbol = context.sample_symbol
    if "." not in symbol:
        suffix = "SH" if symbol.startswith("6") else "SZ"
        symbol = f"{symbol}.{suffix}"
    end = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    query = DailyQuery(symbol=symbol, instrument_type="STOCK", adjustment="none",
                       start=(end - timedelta(days=20)).isoformat(), end=end.isoformat(), request_id="free-history-probe")
    daily(query, ReadContext(context.adapter, context.snapshot, timeout_seconds=5, probe=True))


SOURCE = SourceDefinition("tickflow-free", manifest, factory, tuple(
    DailyBinding(ExactKey("CN", "STOCK", "DAILY_BAR", adjustment), UPSTREAM,
                 f"dsa-v3-cn-stock-tickflow-free-{adjustment}-exact-daily-v1",
                 PROTOCOL, PROTOCOL, MAXIMUM_ROWS, daily,
                 research_calendar_eligible=research_calendar_eligible)
    for adjustment in ("none", "qfq", "hfq")
), dependency_modules=("tickflow",),
    probe_policy=SourceProbePolicy(probe_daily, capabilities=("DAILY_BAR",), adapter_required=True))
