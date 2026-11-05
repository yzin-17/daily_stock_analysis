"""按相同精确价格绑定读取研究身份；来源负责事实，核心只校验关联。"""

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import DailyBinding
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_research_instrument_contract import (
    ResearchInstrumentRequest, correlate_research_instrument,
)


def source_research_binding(key, target):
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if isinstance(binding, DailyBinding) and binding.research_instrument_policy is not None:
        return binding
    return None


def read_source_research_instrument(symbol, market, start, end, data_as_of, key, target):
    binding = source_research_binding(key, target) if target else None
    if binding is None:
        if key.get('adjustment') == 'none':
            raise CapabilityContractError('unsupported_capability', '按股事实需要相同已核对价格来源')
        return None
    if binding.key.market != market:
        raise CapabilityContractError('unsupported_capability', '研究事实需要同一已核对证券身份')
    policy = binding.research_instrument_policy
    if policy.supports_symbol(symbol) is not True:
        raise CapabilityContractError('not_covered', '研究事实未覆盖该证券身份')
    try:
        request = ResearchInstrumentRequest(symbol=symbol, market=market, start=start, end=end, data_as_of=data_as_of)
        return correlate_research_instrument(request, policy.read(request), binding.upstream_source).model_dump()
    except CapabilityContractError:
        raise
    except ValueError:
        raise CapabilityContractError('invalid_response', '研究身份事实与标准合同不符') from None
