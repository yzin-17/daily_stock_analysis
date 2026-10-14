"""来源连接诊断；草稿凭证不写入配置、健康状态或业务路由。"""

import logging
from typing import Any

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, SourceProbeContext
from src.services.thesis_ledger_provider_runtime import (
    PROVIDER_MANIFESTS, ProviderCallError, classify_provider_exception, provider_symbol_for_contract,
)

logger = logging.getLogger(__name__)


def smoke_provider(
    runtime,
    provider_id: str,
    capability: str,
    *,
    credential_snapshot: ProviderCredentialSnapshot | None = None,
    draft_probe: bool | None = None,
) -> dict[str, Any]:
    """Run one bounded, read-only representative call without changing policy."""
    normalized_capability = capability.strip().upper()
    source = CAPABILITY_SOURCES.source(provider_id)
    probe_policy = source.probe_policy if source is not None else None
    if probe_policy is not None and probe_policy.capabilities and normalized_capability not in probe_policy.capabilities:
        probe_policy = None
    if normalized_capability in {"FUND_NAV", "FUND_NAV_HISTORY", "FUND_HOLDINGS"}:
        instrument_type = "MUTUAL_FUND"
    elif probe_policy is not None and normalized_capability in dict(probe_policy.instrument_types):
        instrument_type = dict(probe_policy.instrument_types)[normalized_capability]
    else:
        instrument_type = "STOCK"
    started = runtime.clock()
    if draft_probe is None:
        draft_probe = credential_snapshot is not None
    circuit_key = f"thesis-ledger:{provider_id}:{normalized_capability}:{instrument_type}"
    manifest = PROVIDER_MANIFESTS.get(provider_id, {})
    sample_contract_symbol = (
        "AAPL.US" if "US" in manifest.get("markets", ()) else "600519.SH"
    )
    sample_symbol = provider_symbol_for_contract(sample_contract_symbol)

    def record_failure(code: str) -> None:
        if draft_probe:
            return
        runtime.store.record_health(
            provider_id,
            normalized_capability,
            instrument_type,
            state="degraded",
            circuit=runtime.circuit.state(circuit_key, runtime.clock()),
            error_code=code,
        )

    try:
        if probe_policy is not None:
            if probe_policy.adapter_required:
                adapter = runtime._adapter(provider_id, credential_snapshot, cache=False, probe=True)
                context = SourceProbeContext(normalized_capability, credential_snapshot, sample_symbol, adapter)
            else:
                context = SourceProbeContext(normalized_capability,
                    credential_snapshot or runtime.store.provider_credential_snapshot(provider_id), sample_symbol)
            probe_policy.read(context)
        else:
            _probe_generic(runtime, provider_id, normalized_capability, sample_symbol, credential_snapshot)
    except CapabilityContractError as exc:
        record_failure(exc.code)
        raise ProviderCallError(exc.code, str(exc), retryable=exc.retryable) from None
    except ProviderCallError as exc:
        record_failure(exc.code)
        raise
    except (TimeoutError, ConnectionError, OSError) as exc:
        code = classify_provider_exception(exc)
        record_failure(code)
        raise ProviderCallError(code, "Provider 暂时不可用", retryable=True) from exc
    except Exception as exc:
        code = classify_provider_exception(exc)
        record_failure(code)
        logger.warning("ThesisLedger Provider smoke failure provider=%s capability=%s code=%s", provider_id, normalized_capability, code)
        raise ProviderCallError(code, "Provider smoke 调用失败") from exc
    elapsed_ms = max(0, int((runtime.clock() - started) * 1000))
    if not draft_probe:
        runtime.circuit.success(circuit_key)
        runtime.store.record_health(
            provider_id,
            normalized_capability,
            instrument_type,
            state="healthy",
            circuit="closed",
            consecutive_failures=0,
            latency_ms=elapsed_ms,
        )
    return {
        "status": "healthy",
        "attempted": True,
        "readOnly": True,
        "latencyMs": elapsed_ms,
    }

def _probe_generic(runtime, provider_id, normalized_capability, sample_symbol, credential_snapshot):
    adapter = runtime._adapter(
        provider_id,
        credential_snapshot,
        cache=False,
        probe=True,
    )
    if normalized_capability == "SECURITY_LOT_SIZE":
        from data_provider.security_lot_size import read_security_lot_size
        read_security_lot_size(provider_id, adapter, "00700.HK")
    elif normalized_capability == "REALTIME_QUOTE":
        value = runtime._realtime_quote(
            adapter,
            provider_id,
            sample_symbol,
            None,
            None,
        )
        if value is None or getattr(value, "price", None) is None:
            raise ProviderCallError("not_covered", "Provider 未返回代表性行情")
    elif normalized_capability == "DAILY_BAR":
        daily_data = adapter.get_daily_data(sample_symbol, days=5)
        frame = runtime._daily_frame(daily_data)
        runtime._validate_bars(frame)
    elif normalized_capability in {"FUND_NAV", "FUND_NAV_HISTORY"}:
        from .thesis_ledger_capability_contracts import ReadContext
        from .thesis_ledger_nav_diagnostics import diagnostic_target, read_nav_diagnostic
        key = {"kind": "data", "market": "CN", "assetType": "MUTUAL_FUND", "capability": normalized_capability}
        read_nav_diagnostic(key, diagnostic_target(provider_id, normalized_capability, ProviderCallError), "000001.OF",
                           ReadContext(adapter, credential_snapshot, probe=True), ProviderCallError)
    elif normalized_capability == "FUND_HOLDINGS":
        from .thesis_ledger_capability_contracts import ReadContext
        from .thesis_ledger_fund_holdings_contract_v1 import HoldingsRequest
        from .thesis_ledger_holdings_diagnostics import diagnostic_target, read_holdings_diagnostic
        read_holdings_diagnostic(HoldingsRequest(symbol='000001.OF').route_key(),
            diagnostic_target(provider_id, ProviderCallError), '000001.OF',
            ReadContext(adapter, credential_snapshot, probe=True), ProviderCallError)
    elif normalized_capability == "CHIP_SUMMARY":
        from .thesis_ledger_chip_summary_contract_v1 import ChipRequest
        from .thesis_ledger_chip_summary_query import diagnostic_target, read_chip_query
        from .thesis_ledger_capability_contracts import ReadContext
        target = diagnostic_target(provider_id, ProviderCallError)
        query = ChipRequest(symbol=sample_symbol + '.SH')
        read_chip_query(query, query.route_key(), target,
                        ReadContext(adapter, credential_snapshot, probe=True), ProviderCallError)
    else:
        raise ProviderCallError("unsupported", "Provider 不支持该 Capability")
