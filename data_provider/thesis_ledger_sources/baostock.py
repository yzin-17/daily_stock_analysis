"""BaoStock 独立状态、粗 ST 与基础日期；不将日期塞入价格覆盖或调用分析备用链。"""

from datetime import date, datetime, timezone
import re
import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead
from src.services.thesis_ledger_capability_registry import DailyBinding, ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_status_capabilities_v1 import (
    TRADING_CONTRACT, RISK_CONTRACT, DATES_CONTRACT, TradingResult, RiskResult, DatesResult,
)
from src.services.thesis_ledger_baostock_daily_status import NORMALIZER_REVISION
from src.services.thesis_ledger_baostock_lifecycle import LIFECYCLE_REVISION
from data_provider.baostock_query_process import (
    read_daily_status as read_baostock_daily_status,
    read_basic_dates as read_baostock_lifecycle,
)
from src.services.thesis_ledger_status_query_support_v1 import TradingSupport, RiskSupport, DatesSupport
from src.services.thesis_ledger_status_acquisition import StatusAcquisitionKey

STATUS_SOURCE_REVISION = "baostock-query-history-status-v1"
DATES_SOURCE_REVISION = "baostock-query-stock-basic-v1"
DAILY_PROTOCOL = "dsa-baostock-exact-daily-single-response-v1"


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    capabilities = {"DAILY_BAR": ("STOCK",), "HISTORICAL_TRADING_STATUS": ("STOCK",)}
    capabilities.update({binding.key.capability: ("STOCK",) for binding in BINDINGS})
    return build_provider_manifest("baostock", "BaoStock", capabilities, markets=("CN",), configuration_mode="built_in")


def factory(context):
    from data_provider.baostock_fetcher import BaostockFetcher
    return BaostockFetcher()


def read_daily_frame(adapter, query):
    code, exchange = query.symbol.split(".", 1)
    if exchange not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
        raise ExactDailyError("invalid_request", "中国内地日线需要完整证券代码")
    if exchange == "BJ":
        raise ExactDailyError("not_covered", "BaoStock 当前精确接入仅覆盖沪深市场")
    with adapter._baostock_session() as sdk:
        response = sdk.query_history_k_data_plus(
            code=exchange.lower() + "." + code,
            fields="date,code,open,high,low,close,volume,amount",
            start_date=query.start, end_date=query.end, frequency="d",
            adjustflag={"none": "3", "qfq": "2", "hfq": "1"}[query.adjustment],
        )
        if response.error_code != "0":
            raise ExactDailyError("upstream_failure", "BaoStock 日线查询未通过")
        rows = []
        while response.next():
            rows.append(response.get_row_data())
        raw = pd.DataFrame(rows, columns=response.fields)
        if not raw.empty and not raw["code"].eq(exchange.lower() + "." + code).all():
            raise ExactDailyError("invalid_response", "BaoStock 日线标的身份不一致")
    return normalize_exact_daily(raw, start=query.start, end=query.end, source="baostock")


def daily(query, context):
    try:
        return DailyRead(read_daily_frame(context.adapter, query), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def validate_source_request(query):
    code, market = query.symbol.split('.')
    code = market.lower() + '.' + code
    if not re.fullmatch(r"(?:sh\.(?:60|68)\d{4}|sz\.(?:00|30)\d{4})", code):
        raise CapabilityContractError("not_covered", "BaoStock 该功能仅提供明确沪深股票")
    if hasattr(query, "range"):
        start, end = date.fromisoformat(query.range.start), date.fromisoformat(query.range.end)
        if (end - start).days > 3660 or end > datetime.now(timezone.utc).date():
            raise CapabilityContractError("invalid_request", "状态请求窗口超过来源限制或观察日期")
    if hasattr(query, "detail") and query.detail.kind != "coarseST":
        raise CapabilityContractError("unsupported_capability", "BaoStock 粗 ST 不能提供细分风险警示")


def acquire_daily(query, context):
    try:
        return read_baostock_daily_status(context.adapter, symbol=query.symbol,
                                         start=date.fromisoformat(query.range.start), end=date.fromisoformat(query.range.end))
    except (RuntimeError, OSError):
        raise CapabilityContractError("upstream_unavailable", "BaoStock 状态查询暂不可用") from None
    except (ValueError, TypeError, KeyError):
        raise CapabilityContractError("invalid_response", "BaoStock 状态身份或字段无效") from None


def cached_acquisition(query, context, method, adapter_revision, source_revision, load, cacheable):
    if context.acquisition_cache is None or context.control_epoch is None:
        return load()
    window = getattr(query, "range", None)
    key = StatusAcquisitionKey("baostock", "baostock", method, query.symbol,
                               window.start if window else None, window.end if window else None,
                               adapter_revision, source_revision, *context.control_epoch)
    return context.acquisition_cache.read(key, load, cacheable)


def window_raw(query, context):
    validate_source_request(query)
    return cached_acquisition(query, context, "query_trade_dates+query_history_k_data_plus:date,code,tradestatus,isST:d:3",
                              NORMALIZER_REVISION, STATUS_SOURCE_REVISION, lambda: acquire_daily(query, context),
                              lambda value: any(item["state"] != "unknown" or item["isST"] in {"0", "1"} for item in value["days"]))


def evidence(raw, kind, identifier):
    return {"id": identifier, "kind": kind, "sha256": raw["responseSha256"],
            "observedAt": raw["observedAt"], "availableAt": None, "reference": None, "hashScope": "normalizedResponse"}


def metadata(query, raw, adapter_revision, source_revision, records):
    return {**query.model_dump(), "provenance": {"adapterRevision": adapter_revision, "sourceRevision": source_revision},
            "clock": {"observedAt": raw["observedAt"], "upstreamAt": None,
                      "timezone": "Asia/Shanghai", "basis": "sessionDate" if hasattr(query, "range") else "effectiveDate"},
            "evidence": records}


def window_envelope(query, raw, unknown_dates):
    record = "baostock-status:" + raw["responseSha256"]
    calendar = "baostock-calendar:" + raw["responseSha256"]
    result = metadata(query, raw, NORMALIZER_REVISION, STATUS_SOURCE_REVISION,
                      [evidence(raw, "record", record), evidence(raw, "calendar", calendar)])
    result["coverage"] = {"range": query.range.model_dump(), "expectedDates": raw["expectedSessions"],
                          "calendarUnknownDates": raw["coverage"]["calendarUnknownDates"],
                          "unknownDates": unknown_dates, "calendarEvidenceRefs": [calendar],
                          "complete": not raw["coverage"]["calendarUnknownDates"] and not unknown_dates}
    return result, record


def trading(query, context):
    raw = window_raw(query, context)
    value, record = window_envelope(query, raw, raw["coverage"]["statusUnknownDates"])
    value["days"] = [{"date": item["date"], "state": item["state"], "intradayHalts": [],
                      "evidenceRefs": [record] if item["state"] != "unknown" else []} for item in raw["days"]]
    return TradingResult.model_validate(value)


def risk(query, context):
    if query.detail.kind != "coarseST":
        raise CapabilityContractError("unsupported_capability", "BaoStock 粗 ST 不能提供细分风险警示")
    raw = window_raw(query, context)
    value, record = window_envelope(query, raw, raw["coverage"]["stUnknownDates"])
    flags = {"0": "notFlagged", "1": "flagged"}
    value["days"] = [{"date": item["date"], "coarseST": flags.get(item["isST"]),
                      "delisting": "unknown", "other": "unknown",
                      "evidenceRefs": [record] if item["isST"] in flags else []} for item in raw["days"]]
    return RiskResult.model_validate(value)


def basic_dates(query, context):
    validate_source_request(query)
    raw = cached_acquisition(query, context, "query_stock_basic:code,ipoDate,outDate,type", LIFECYCLE_REVISION,
                             DATES_SOURCE_REVISION, lambda: read_baostock_lifecycle(context.adapter, query.symbol),
                             lambda value: value.get("responseSha256") is not None and
                             "known" in (value.get("ipoDateStatus"), value.get("outDateStatus")))
    if "responseSha256" not in raw:
        value = metadata(query, {"observedAt": datetime.now(timezone.utc).isoformat()}, LIFECYCLE_REVISION, DATES_SOURCE_REVISION, [])
        value["dates"] = raw
    else:
        record = "baostock-basic:" + raw["responseSha256"]
        value = metadata(query, raw, LIFECYCLE_REVISION, DATES_SOURCE_REVISION, [evidence(raw, "record", record)])
        value["dates"] = {field: raw[field] for field in ("ipoDate", "outDate", "ipoDateStatus", "outDateStatus")}
        value["dates"]["sourceRecordId"] = record
    return DatesResult.model_validate(value)


BINDINGS = (
    QueryBinding(TRADING_CONTRACT, ExactKey("CN", "STOCK", "DAILY_TRADING_STATUS"), "baostock", NORMALIZER_REVISION, STATUS_SOURCE_REVISION, trading, gated=False, validate_parameters=validate_source_request,
                 query_support=TradingSupport(maximumWindowDays=3661)),
    QueryBinding(RISK_CONTRACT, ExactKey("CN", "STOCK", "RISK_WARNING"), "baostock", NORMALIZER_REVISION, STATUS_SOURCE_REVISION, risk, gated=False, validate_parameters=validate_source_request,
                 query_support=RiskSupport(detailKinds=["coarseST"], riskCategories=[])),
    QueryBinding(DATES_CONTRACT, ExactKey("CN", "STOCK", "SECURITY_BASIC_DATES"), "baostock", LIFECYCLE_REVISION, DATES_SOURCE_REVISION, basic_dates, gated=False, validate_parameters=validate_source_request,
                 query_support=DatesSupport(fields=["ipoDate", "outDate"])),
)
from src.services.thesis_ledger_legacy_status_contract_v1 import CONTRACT as LEGACY_CONTRACT
from data_provider.baostock_legacy_status import read_status as read_legacy_status

SOURCE = SourceDefinition("baostock", manifest, factory, (
    QueryBinding(LEGACY_CONTRACT, ExactKey('CN','STOCK','HISTORICAL_TRADING_STATUS'), 'baostock',
        'baostock-daily-flags-v1', 'baostock-query-history-status-v1', read_legacy_status, admission_required=False),
    *BINDINGS,
    *(DailyBinding(ExactKey("CN", "STOCK", "DAILY_BAR", adjustment), "baostock",
                   f"dsa-v3-cn-stock-baostock-{adjustment}-exact-daily-v1",
                   DAILY_PROTOCOL, DAILY_PROTOCOL, None, daily, gated=True)
      for adjustment in ("none", "qfq", "hfq")),
), ("data_provider.baostock_fetcher", "BaostockFetcher"), dependency_modules=("baostock",))
