"""efinance/Eastmoney 六个既有精确日线；原 SDK 参数和字段校验归来源拥有。"""

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, provider_symbol_for_contract
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, QueryBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_fund_nav_contract_v1 import CONTRACT as NAV_CONTRACT
from src.services.thesis_ledger_fund_nav_history_contract_v1 import CONTRACT as NAV_HISTORY_CONTRACT

PROTOCOL = "dsa-efinance-exact-daily-single-response-v1"


def manifest():
    from src.services.thesis_ledger_efinance_manifest import build_efinance_manifest
    return build_efinance_manifest()


def factory(context):
    from data_provider.efinance_fetcher import EfinanceFetcher
    return EfinanceFetcher()


def read_frame(request, timeout_seconds=4.5):
    import efinance as ef
    from data_provider.efinance_fetcher import _ef_call_with_timeout

    code, exchange = request.symbol.split(".", 1)
    if exchange not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
        raise ExactDailyError("invalid_request", "中国内地日线需要完整证券代码")
    raw = _ef_call_with_timeout(
        ef.stock.get_quote_history, stock_codes=("1" if exchange == "SH" else "0") + "." + code,
        beg=request.start.replace("-", ""), end=request.end.replace("-", ""), klt=101,
        fqt={"none": 0, "qfq": 1, "hfq": 2}[request.adjustment], quote_id_mode=True,
        use_id_cache=False, timeout=min(timeout_seconds, 4.5),
    )
    if "股票代码" in raw and not raw["股票代码"].astype(str).str.zfill(6).eq(code).all():
        raise ExactDailyError("invalid_response", "efinance 日线标的身份不一致")
    columns = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
               "收盘": "close", "成交量": "volume", "成交额": "amount"}
    return normalize_exact_daily(raw, start=request.start, end=request.end, source="eastmoney", columns=columns)


def daily(query, context):
    try:
        return DailyRead(read_frame(query, context.timeout_seconds), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def quote(query, context):
    symbol = provider_symbol_for_contract(query.symbol)
    try:
        if query.instrument_type == "ETF":
            value = context.adapter.get_realtime_quote_single_symbol(symbol)
        else:
            value = context.adapter._get_realtime_snapshot_quote(symbol)
    except TimeoutError:
        raise CapabilityContractError("transient_failure", "efinance 单标报价超时") from None
    except Exception:
        raise CapabilityContractError("upstream_failure", "efinance 单标报价读取失败") from None
    if value is None:
        raise CapabilityContractError("not_covered", "efinance 单标快照未覆盖目标")
    return value


def latest_nav(query, context):
    from data_provider.eastmoney_nav_projection import latest_nav as project
    return project(context.adapter.get_fund_nav_history(query.symbol.removesuffix('.OF')), query, 'efinance', 'eastmoney')


def history_nav(query, context):
    from data_provider.eastmoney_nav_projection import history_nav as project
    return project(context.adapter.get_fund_nav_history(query.symbol.removesuffix('.OF')), query, 'efinance', 'eastmoney')


SOURCE = SourceDefinition("efinance", manifest, factory, tuple(
    DailyBinding(ExactKey("CN", asset, "DAILY_BAR", adjustment), "eastmoney",
                 f"dsa-v3-cn-{asset.lower()}-efinance-{adjustment}-exact-daily-v1",
                 PROTOCOL, PROTOCOL, None, daily, gated=True)
    for asset, adjustment in (("STOCK", "none"), ("STOCK", "qfq"), ("STOCK", "hfq"),
                              ("ETF", "none"), ("ETF", "qfq"), ("ETF", "hfq"))
) + (
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "eastmoney",
        "efinance-stock-single-em-v2", "efinance-stock-quote-em-v1", quote),
    QuoteBinding(ExactKey("CN", "ETF", "REALTIME_QUOTE"), "eastmoney",
        "efinance-etf-single-em-v1", "efinance-snapshot-em-v1", quote),
    QueryBinding(NAV_CONTRACT, ExactKey('CN', 'MUTUAL_FUND', 'FUND_NAV'), 'eastmoney',
        'efinance-fund-nav-em-v1', 'efinance-fund-nav-em-v1', latest_nav, admission_required=False),
    QueryBinding(NAV_HISTORY_CONTRACT, ExactKey('CN', 'MUTUAL_FUND', 'FUND_NAV_HISTORY'), 'eastmoney',
        'efinance-fund-nav-raw-v1', 'eastmoney-fund-nav-raw-v1', history_nav, admission_required=False),
), ("data_provider.efinance_fetcher", "EfinanceFetcher"), dependency_modules=("efinance",),
    quote_source_aliases=(('efinance', 'eastmoney'), ('eastmoney', 'eastmoney')))
