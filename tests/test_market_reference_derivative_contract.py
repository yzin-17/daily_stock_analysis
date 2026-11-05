"""衍生品资料的代码、Data／Bar 边界与路由身份验证。"""

import pytest
from pydantic import ValidationError
from src.services.thesis_ledger_reference_contract_v1 import ReferenceRequest
from src.services.thesis_ledger_reference_scope_v1 import asset_type, runtime_symbol
from src.services.thesis_ledger_route_asset_types import route_asset_type_allowed
from src.services.thesis_ledger_control import normalize_policy_v3


@pytest.mark.parametrize('symbol', ['RB0', 'M0', 'IF2612'])
def test_future_identity(symbol):
    query = ReferenceRequest(requestId='x', dataset='futures-quote', symbol=symbol)
    assert query.route_key()['assetType'] == 'FUTURE'


@pytest.mark.parametrize('symbol', ['RB2613', 'IF', 'nf_RB0', '600519.SH', 'RB0&list=secret'])
def test_future_rejects_wrong_identity(symbol):
    with pytest.raises(ValidationError):
        ReferenceRequest(requestId='x', dataset='futures-quote', symbol=symbol)


@pytest.mark.parametrize('symbol', ['510050.SH', '510300.SH', '588000.SH', '510500.SH'])
def test_option_underlying_identity(symbol):
    query = ReferenceRequest(requestId='x', dataset='etf-option-contracts', symbol=symbol)
    assert query.route_key()['assetType'] == 'OPTION'


def test_option_contract_is_not_stock_or_underlying():
    ReferenceRequest(requestId='x', dataset='etf-option-quote', symbol='10012440')
    for dataset, symbol in [('etf-option-quote', '510050.SH'), ('etf-option-contracts', '159915.SZ'), ('financial-balance', '10012440')]:
        with pytest.raises(ValidationError):
            ReferenceRequest(requestId='x', dataset=dataset, symbol=symbol)


def test_single_day_and_current_quote_dates():
    with pytest.raises(ValidationError):
        ReferenceRequest(requestId='x', dataset='futures-daily-shfe')
    query = ReferenceRequest(requestId='x', dataset='futures-daily-shfe', start='2026-11-03', end='2026-11-03')
    assert runtime_symbol(query) == 'CN.FUTURE'
    with pytest.raises(ValidationError):
        ReferenceRequest(requestId='x', dataset='futures-quote', symbol='RB0', start='2026-11-03')
    assert asset_type('convertible-bond-reference') == 'CONVERTIBLE_BOND'


@pytest.mark.parametrize('asset', ['FUTURE', 'OPTION'])
def test_control_data_only_extension(asset):
    assert route_asset_type_allowed('data', asset)
    assert not route_asset_type_allowed('bar', asset)
    policy = {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'x', 'revision': 1, 'enabled': True,
              'routes': [{'key': {'kind': 'data', 'market': 'CN', 'assetType': asset, 'capability': 'REFERENCE_TEST'},
                          'enabled': True, 'targets': [{'providerId': 'akshare', 'upstreamSource': 'sina'}]}]}
    assert normalize_policy_v3(policy)['routes'][0]['key']['assetType'] == asset
