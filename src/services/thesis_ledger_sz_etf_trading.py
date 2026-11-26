"""深市 ETF 状态复用已启用富途账号；不修改现有报价来源或路由。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from data_provider.futu_market import FutuMarketAdapter, FutuMarketError
from .thesis_ledger_control import ControlContractError
from .thesis_ledger_current_data_route import CurrentDataRouteError
from .thesis_ledger_feature_policy import feature_provider_epoch


def read_sz_etf_trading(store, symbol):
    now = datetime.now(timezone.utc)
    result = {'symbol': symbol, 'assetType': 'ETF',
              'day': now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
              'observedAt': now.isoformat(), 'state': 'unknown',
              'evidence': {'provider': 'futu', 'reason': 'current_status_unavailable'}}
    try:
        before = feature_provider_epoch(store, ['futu'])
        if not before['futu'][1]:
            result['evidence']['reason'] = 'provider_disabled'
            return result
        snapshot = store.provider_credential_snapshot('futu')
        if not snapshot.values:
            result['evidence']['reason'] = 'credential_missing'
            return result
        adapter = FutuMarketAdapter(**snapshot.values)
        value = adapter.get_current_trading_state(symbol)
        if feature_provider_epoch(store, ['futu']) != before:
            result['evidence']['reason'] = 'source_control_changed'
            return result
        value['evidence']['configVersion'] = before['futu'][0]
        return value
    except (FutuMarketError, ControlContractError, CurrentDataRouteError, KeyError, TypeError, ValueError) as error:
        result['evidence']['reason'] = getattr(error, 'code', 'current_status_unavailable')
        return result
