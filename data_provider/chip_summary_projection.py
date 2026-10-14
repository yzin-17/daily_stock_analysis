"""原 ChipDistribution 到标准估算摘要的来源输入适配。"""

from datetime import datetime, timezone
import math

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_chip_summary_contract_v1 import ChipResult


def project(value, query, provider, upstream, methodology):
    if value is None:
        raise CapabilityContractError('not_covered', '来源未返回筹码摘要')
    try:
        code = getattr(value, 'code', None)
        if code is not None and code not in {query.symbol, query.symbol.split('.')[0]}:
            raise ValueError('来源筹码标的不一致')
        def number(name):
            raw = getattr(value, name, None)
            if isinstance(raw, bool):
                raise ValueError('布尔值不是筹码数值')
            result = float(raw)
            if not math.isfinite(result):
                raise ValueError('筹码数值非有限')
            return result
        reported = getattr(value, 'date', None)
        return ChipResult(symbol=query.symbol, average_cost=number('avg_cost'), profit_ratio=number('profit_ratio'),
            range70=(number('cost_70_low'), number('cost_70_high')),
            range90=(number('cost_90_low'), number('cost_90_high')),
            concentration70=number('concentration_70'), concentration90=number('concentration_90'),
            source_reported_date=str(reported) if reported else None,
            provider=provider, upstream_source=upstream, methodology=methodology,
            observed_at=datetime.now(timezone.utc).isoformat())
    except (ValueError, TypeError) as error:
        raise CapabilityContractError('invalid_response', '来源筹码未通过标准合同') from error
