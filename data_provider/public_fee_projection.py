"""原只读公共费用读取器到标准结果的适配；不修改源包或金额。"""

from pydantic import ValidationError
from src.services.thesis_ledger_public_fees import PublicFeeSourceError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_public_fees_contract_v1 import FeeResult


def read_fees(query, context):
    try:
        raw = context.adapter.read(query.model_dump(mode='json'))
    except PublicFeeSourceError as error:
        if error.code == 'insufficient_coverage' and context.database_path:
            from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
            from src.services.local_rule_demand_refresh import refresh_selection, material_adapter
            key, target = query.routeKey.model_dump(), query.routeTarget.model_dump()
            source = CAPABILITY_SOURCES.source(target['providerId'])
            binding = CAPABILITY_SOURCES.resolve(key, target)
            selected, _, material = refresh_selection([(source, binding)], query, context)
            raw = material_adapter(selected, material).read(query.model_dump(mode='json'))
        else:
            raise CapabilityContractError(error.code, str(error)) from None
    try:
        return FeeResult.model_validate(raw)
    except ValidationError:
        raise CapabilityContractError('invalid_response', '来源公共费用未通过标准合同') from None
