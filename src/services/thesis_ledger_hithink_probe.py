"""HiThink 各能力的单次只读连接测试；不授予路由或历史覆盖准入。"""

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_hithink_quote import HiThinkQuoteError, HiThinkSnapshotAdapter
from src.services.thesis_ledger_hithink_stock import HiThinkStockError, HiThinkStockHistoricalAdapter
from data_provider.hithink_fund_dividend_reader import (
    HiThinkFundDividendReadError, fetch_hithink_fund_dividends,
)


def probe_hithink_capability(capability, snapshot, *, error_type=None):
    if error_type is None:
        from src.services.thesis_ledger_provider_runtime import ProviderCallError
        error_type = ProviderCallError

    if (not isinstance(snapshot, ProviderCredentialSnapshot)
            or snapshot.provider_id != "hithink" or snapshot.source not in {"environment", "control"}
            or snapshot.method != "api_key" or not snapshot.values.get("apiKey", "").strip()):
        raise error_type("not_configured", "HiThink API Key 未配置")
    api_key = snapshot.values["apiKey"]
    try:
        if capability == "REALTIME_QUOTE":
            HiThinkSnapshotAdapter(api_key=api_key).fetch_quote("600519.SH", "STOCK")
        elif capability == "DAILY_BAR":
            # 单个已知交易日仅用于检查历史接口，不作为业务日历或覆盖事实。
            frame = HiThinkStockHistoricalAdapter(api_key, timeout_seconds=4.5).fetch_daily_bars(
                "600519.SH", "2020-01-02", "2020-01-02", "qfq",
                expected_sessions=["2020-01-02"], calendar_revision="connection-probe-single-session-v1",
            )
            if frame.empty:
                raise error_type("not_covered", "HiThink 未返回测试行情")
        elif capability == "CASH_DISTRIBUTION":
            fetch_hithink_fund_dividends(
                "510300.SH", fund_type="exchange", api_key=api_key, timeout_seconds=4.5,
            )
        else:
            raise error_type("unsupported", "HiThink 不支持该连接测试能力")
    except (HiThinkQuoteError, HiThinkStockError, HiThinkFundDividendReadError) as error:
        raise error_type(error.code, "HiThink 连接测试未通过") from None
