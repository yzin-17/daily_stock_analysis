"""BaoStock旧聚合状态复用原隔离/缓存读取，验证旧基础日期角色。"""

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_trading_status import TradingStatusSourceError
from .legacy_status_projection import validated


def read_status(query, context):
    from src.services.thesis_ledger_baostock_status_source import read_baostock_status_response
    try:
        raw = read_baostock_status_response(context.adapter, query.model_dump(mode='json'))
    except TradingStatusSourceError as error:
        raise CapabilityContractError(error.code, str(error)) from None
    result = validated(raw)
    life = result.basicLifecycle
    if life is not None and life.sourceRecordId:
        record = next(item for item in result.sourceRecords if item.id == life.sourceRecordId)
        if (record.providerId, record.upstreamSource, record.queryMethod, record.queryFields, record.normalizerRevision) != (
                'baostock', 'baostock', 'query_stock_basic', 'code,ipoDate,outDate,type', 'baostock-basic-dates-v1'):
            raise CapabilityContractError('invalid_response', '基础日期引用不是原单证券基础资料响应')
    return result
