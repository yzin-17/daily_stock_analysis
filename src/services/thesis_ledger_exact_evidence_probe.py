"""官方事实和事件来源诊断；只消费真实数据包及当前独立证据。"""

from copy import deepcopy
from datetime import datetime, timezone
from types import MappingProxyType
from pydantic import ValidationError

from src.services.thesis_ledger_provider_runtime import ProviderCallError
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import EventBinding
from src.services.thesis_ledger_capability_contracts import ExactProbeSample
from src.services.thesis_ledger_event_execution_context_v1 import EventCredentialContext, EventProbeReadContext


def read_evidence_probe(runtime, key, target, snapshot):
    if snapshot != runtime.store.provider_credential_snapshot(target["providerId"]):
        raise ProviderCallError("not_admitted", "草稿凭证未绑定当前事件准入证据")
    admission = deepcopy(runtime._current_market_v3_admission(key, target))
    if not admission or admission.get("admissionState") != "admitted":
        raise ProviderCallError("not_admitted", "缺少该精确事件来源的当前准入与映射证据")
    symbols = admission.get("scopeSymbols")
    if not isinstance(symbols, list) or not symbols:
        raise ProviderCallError("not_admitted", "事件准入缺少实际标的范围")
    symbol, start, end = symbols[0], admission.get("scopeDateFrom"), admission.get("scopeDateTo")
    try:
        sample = ExactProbeSample(symbol=symbol, start=start, end=end)
    except ValidationError:
        raise ProviderCallError('admission_invalid', '事件准入检查样本无效') from None
    from src.services.thesis_ledger_control import _secret_key
    provider = target["providerId"]
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if not isinstance(binding, EventBinding) or binding.probe_read is None:
        raise ProviderCallError("not_adapted", "事件来源缺少精确检查读取器")
    credentials = EventCredentialContext(runtime.store.database_path,
        lambda: runtime._current_market_v3_admission(key, target),
        lambda: runtime.store.provider_credential_snapshot(provider), _secret_key,
        lambda credential: runtime._adapter(provider, snapshot=credential, cache=False, probe=True))
    context = EventProbeReadContext(key['capability'], symbol, start, end, datetime.now(timezone.utc).isoformat(),
        MappingProxyType(deepcopy(admission)), credentials)
    if binding.probe_read(context) is not None:
        raise ProviderCallError('invalid_response', '事件诊断读取器应只执行读取')
    if runtime._current_market_v3_admission(key, target) != admission:
        raise ProviderCallError("admission_invalid", "事件检查期间准入发生变化")
    return sample.model_dump()


