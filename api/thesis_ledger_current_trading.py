"""当前证券状态读取复用对应实时行情功能开关。"""

from typing import Literal
from fastapi import APIRouter, Depends, Query
from api.thesis_ledger_auth import require_contract_token, _error
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_current_data_route import select_current_data_targets, CurrentDataRouteError
from src.services.thesis_ledger_current_trading import current_trading
from src.services.thesis_ledger_sz_etf_trading import read_sz_etf_trading
from src.services.thesis_ledger_dependency_facts import validate_cn_stock, DependencyFactError

router = APIRouter(prefix='/thesis-ledger')


@router.get('/market/trading-observation', dependencies=[Depends(require_contract_token)])
def trading_observation(symbol: str = Query(pattern=r'^\d{6}\.(SH|SZ|BJ)$'),
                        asset_type: Literal['STOCK', 'ETF'] = Query(alias='assetType')):
    try:
        validate_cn_stock(symbol, 'CN', asset_type, lambda value: value)
    except DependencyFactError as error:
        _error(error.code, str(error), error.status_code)
    store = ThesisLedgerControlStore()
    policy = store.effective_policy_v3()
    try:
        if policy and any(r.get('enabled') is False and r.get('key') == {
                'kind': 'data', 'market': 'CN', 'assetType': asset_type, 'capability': 'REALTIME_QUOTE'}
                for r in policy.get('routes', [])):
            raise CurrentDataRouteError('disabled', '对应行情功能已关闭')
        select_current_data_targets(policy, capability='REALTIME_QUOTE', instrument_type=asset_type, symbol=symbol)
    except CurrentDataRouteError:
        _error('not_selected', '对应实时行情功能未启用或无可用来源', 503)
    result = current_trading(symbol, asset_type, sz_etf_reader=lambda code: read_sz_etf_trading(store, code)) \
        if asset_type == 'ETF' and symbol.endswith('.SZ') else current_trading(symbol, asset_type)
    after = store.effective_policy_v3()
    # appliedAt 是每次投影的生成时间；其余控制及来源资格字段仍参与一致性检查。
    before_control = {key: value for key, value in (policy or {}).items() if key != 'appliedAt'}
    after_control = {key: value for key, value in (after or {}).items() if key != 'appliedAt'}
    if after_control != before_control:
        _error('policy_changed', '读取期间数据策略已变化', 409)
    return result
