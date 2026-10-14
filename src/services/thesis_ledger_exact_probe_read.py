"""精确来源的有界诊断读取；复用生产读取器，不保存业务状态。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import MappingProxyType
from pydantic import ValidationError

from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext, ExactProbeReadContext, ExactProbeSample


def probe_sample(key):
    symbols = {("CN", "STOCK"): "600519.SH", ("CN", "ETF"): "510300.SH",
               ("CN", "MUTUAL_FUND"): "000001.OF", ("HK", "STOCK"): "00700.HK",
               ("HK", "ETF"): "02800.HK", ("US", "STOCK"): "AAPL.US",
               ("US", "ETF"): "SPY.US", ("US", "INDEX"): "^GSPC.US"}
    symbol = symbols.get((key["market"], key["assetType"]))
    if symbol is None:
        raise ProviderCallError("not_adapted", "该资产尚无精确检查标的")
    timezone = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[key["market"]]
    end = datetime.now(ZoneInfo(timezone)).date() - timedelta(days=1)
    return symbol, (end - timedelta(days=7)).isoformat(), end.isoformat()


def probe_policy_revision(runtime):
    read = getattr(getattr(runtime, 'store', None), 'effective_policy_v3', None)
    policy = read() if callable(read) else None
    revision = policy.get('revision') if isinstance(policy, dict) else None
    if type(revision) is int and 1 <= revision <= 9007199254740991:
        return revision
    return None


def read_exact_probe(runtime, key, target, snapshot):
    provider, source, capability = target["providerId"], target["upstreamSource"], key["capability"]
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if binding is not None:
        read = CAPABILITY_SOURCES.source(provider).exact_probe_read
        if read is not None:
            try:
                revision = probe_policy_revision(runtime)
                value = read(ExactProbeReadContext(MappingProxyType(dict(key)), MappingProxyType(dict(target)), revision))
                if value is not None:
                    if revision is not None and probe_policy_revision(runtime) != revision:
                        raise ProviderCallError('stale_revision', '来源检查期间政策代次发生变化')
                    return ExactProbeSample.model_validate(value).model_dump()
            except CapabilityContractError as error:
                raise ProviderCallError(error.code, str(error), retryable=error.retryable, diagnostics=error.diagnostics) from None
            except ValidationError:
                raise ProviderCallError('invalid_response', '来源精确检查样本无效') from None
    if capability in {"CASH_DISTRIBUTION", "SPLIT_EVENT"}:
        from src.services.thesis_ledger_exact_evidence_probe import read_evidence_probe
        return read_evidence_probe(runtime, key, target, snapshot)
    symbol, start, end = probe_sample(key)
    if capability == 'CHIP_SUMMARY':
        from .thesis_ledger_chip_summary_contract_v1 import ChipRequest
        from .thesis_ledger_chip_summary_query import read_chip_query
        adapter = runtime._adapter(provider, snapshot=snapshot, cache=False, probe=True)
        read_chip_query(ChipRequest(symbol=symbol), key, target,
                        ReadContext(adapter, snapshot, probe=True), ProviderCallError)
        return {'symbol': symbol}
    if capability == 'FUND_HOLDINGS':
        from .thesis_ledger_holdings_diagnostics import read_holdings_diagnostic
        adapter = runtime._adapter(provider, snapshot=snapshot, cache=False, probe=True)
        read_holdings_diagnostic(key, target, symbol,
            ReadContext(adapter, snapshot, probe=True), ProviderCallError)
        return {'symbol': symbol}
    if capability in {"FUND_NAV", "FUND_NAV_HISTORY"}:
        from .thesis_ledger_nav_diagnostics import read_nav_diagnostic
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        adapter = runtime._adapter(provider, snapshot=snapshot, cache=False, probe=True)
        read_nav_diagnostic(key, target, symbol,
            ReadContext(adapter, snapshot, probe=True, database_path=getattr(runtime.store, "database_path", None)),
            ProviderCallError, start=start, today=today)
        return {"symbol": symbol, "start": start, "end": today}
    request = ThesisLedgerDataRequest(capability, symbol, start=start, end=end, instrument_type=key["assetType"],
                                     timeframe=key.get("timeframe"), adjustment=key.get("adjustment"),
                                     parameters={"interactive_chart": True})
    adapter = runtime._adapter(provider, snapshot=snapshot, cache=False, probe=True, market_v3_route_key=key)
    if capability == "DAILY_BAR":
        if CAPABILITY_SOURCES.resolve(key, target) is None:
            raise ProviderCallError("not_adapted", "来源缺少标准精确日线绑定")
        try:
            value = CAPABILITY_SOURCES.daily(request, key, target,
                ReadContext(adapter, snapshot, timeout_seconds=4.5, probe=True,
                            database_path=getattr(runtime.store, "database_path", None))).frame
        except CapabilityContractError as error:
            raise ProviderCallError(error.code, str(error), retryable=error.retryable, diagnostics=error.diagnostics) from None
    elif capability == "SECURITY_LOT_SIZE":
        from .thesis_ledger_security_lot_query import read_lot
        from .thesis_ledger_security_lot_contract_v1 import LotRequest
        read_lot(LotRequest(symbol=symbol), key, target, ReadContext(adapter, snapshot, probe=True), ProviderCallError)
        return {"symbol": symbol}
    elif capability == "SECURITY_STATIC_INFO":
        from .thesis_ledger_security_static_query import read_static
        from .thesis_ledger_security_static_contract_v1 import StaticRequest
        read_static(StaticRequest(symbol=symbol, instrument_type=key["assetType"]), key, target,
                    ReadContext(adapter, snapshot, probe=True), ProviderCallError)
        return {"symbol": symbol}
    elif capability == "REALTIME_QUOTE":
        value = runtime._realtime_quote(adapter, provider, symbol, key["assetType"], source)
    else:
        raise ProviderCallError("not_adapted", "来源未登记该检查读取器")
    if capability == "DAILY_BAR":
        frame = runtime._validate_bars(runtime._daily_frame(value))
        if frame.attrs.get("upstream_source") != source:
            raise ProviderCallError("invalid_response", "检查返回的实际日线来源不一致")
        dates = [str(row["date"])[:10] for _, row in frame.iterrows()]
        if dates != sorted(set(dates)) or any(not start <= day <= end for day in dates):
            raise ProviderCallError("invalid_response", "检查日线日期重复、无序或越界")
    elif capability == "REALTIME_QUOTE":
        runtime._validate_quote(value)
    if capability in {"REALTIME_QUOTE", "FUND_HOLDINGS", "CHIP_SUMMARY"}:
        return {"symbol": symbol}
    return {"symbol": symbol, "start": start, "end": end}
