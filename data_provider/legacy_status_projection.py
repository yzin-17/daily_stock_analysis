"""原状态读取器投影标准结果；源证据不通过字段缺省补造。"""

from pydantic import ValidationError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_legacy_status_contract_v1 import StatusResult
from src.services.thesis_ledger_trading_status import TradingStatusSourceError


def validated(raw):
    try:
        return StatusResult.model_validate(raw)
    except ValidationError:
        raise CapabilityContractError('invalid_response', '来源聚合状态未通过标准合同') from None


def read_status(query, context):
    try:
        raw = context.adapter.read(query.model_dump(mode='json'))
    except TradingStatusSourceError as error:
        raise CapabilityContractError(error.code, str(error)) from None
    return validated(raw)


def read_intraday_status(query, context):
    result = read_status(query, context)
    expected = {'HK':'Asia/Hong_Kong', 'US':'America/New_York'}[query.routeKey.market]
    if result.provenance.granularity != 'intraday' or result.provenance.timezone != expected or any(day.state != 'unknown' for day in result.days):
        raise CapabilityContractError('invalid_response', '此来源盘中观察不能声明全天状态')
    return result
