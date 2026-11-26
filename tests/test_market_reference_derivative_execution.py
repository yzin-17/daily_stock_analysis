"""新资产身份经过真实本地 Control、runtime 与 HTTP 接缝，外部响应受控。"""

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_reference_query import build_reference_query_router
from src.services.thesis_ledger_reference_contract_v1 import ReferenceRequest
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from data_provider.market_reference_bindings import READERS, guarded
from data_provider.market_reference_derivatives_sources import SOURCES
from data_provider import market_reference_public_common as common
from data_provider import market_reference_futures_rank as ranks
from tests.test_configured_market_routes import apply, no_admissions, store
from tests.derivative_reference_samples import future_fields, greeks_fields, convertible_row, sina


@pytest.mark.parametrize('dataset,symbol,asset,body', [
    ('futures-quote', 'RB0', 'FUTURE', sina('nf_RB0', future_fields())),
    ('etf-option-greeks', '10012440', 'OPTION', sina('CON_SO_10012440', greeks_fields())),
    ('convertible-bond-reference', None, 'CONVERTIBLE_BOND', json.dumps({'success': True, 'code': 0,
       'result': {'count': 1, 'pages': 1, 'data': [convertible_row()]}}).encode()),
])
def test_normal_http_uses_new_exact_route_and_preserves_admission(store, monkeypatch, dataset, symbol, asset, body):
    query = ReferenceRequest(requestId='derivative-local', dataset=dataset, symbol=symbol)
    provider, source, _ = SOURCES[dataset]
    applied = apply(store, query.route_key(), {'providerId': provider, 'upstreamSource': source})
    assert applied['effective']['routes'][0]['key']['assetType'] == asset
    assert applied['effective']['routes'][0]['targets'][0]['eligible'] is True
    source_read = Mock(return_value=body)
    monkeypatch.setattr(common, 'http_bytes', source_read)
    runtime = ThesisLedgerProviderRuntime(store, adapters={'akshare': object()})
    app = FastAPI();app.dependency_overrides[require_contract_token] = lambda: None
    app.include_router(build_reference_query_router(lambda: runtime), prefix='/api/v1/thesis-ledger')
    response = TestClient(app).post('/api/v1/thesis-ledger/market/reference',
                 json=query.model_dump() | {'contractVersion': 1, 'consumer': 'thesis-ledger'})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['dataset'] == dataset and value['routeTarget']['upstreamSource'] == source
    assert value['items'] and value['dataVersion'] == 'market-reference-v1'
    assert source_read.call_count == 1
    no_admissions(store)


@pytest.mark.parametrize('dataset', ['futures-quote', 'etf-option-greeks', 'convertible-bond-reference'])
def test_frontend_headers_not_ignored_for_new_assets(tmp_path, dataset):
    path = tmp_path / 'config.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE thesis_ledger_provider_config(provider_id TEXT, settings_json TEXT)')
        db.execute('INSERT INTO thesis_ledger_provider_config VALUES (?, ?)', ('akshare', json.dumps({'headers': {'X-Test': 'configured'}})))
    outbound = Mock()
    with pytest.raises(CapabilityContractError) as error:
        guarded(outbound)(SimpleNamespace(dataset=dataset), SimpleNamespace(database_path=str(path)))
    assert error.value.code == 'configuration_failure'
    outbound.assert_not_called()


def test_cffex_missing_one_product_does_not_return_partial_rank(monkeypatch):
    calls = []
    def read(url):
        code = url.rsplit('/', 1)[1].split('_')[0];calls.append(code)
        if code == 'TL':
            raise CapabilityContractError('upstream_failure', '受控缺文件')
        return ('交易日,合约,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,\n'
                f'20261103,{code}2611,1,成交会员,0,0,持买会员,20,0,持卖会员,20,0').encode('gb18030')
    monkeypatch.setattr(ranks, 'PublicRead', lambda seconds: SimpleNamespace(read=read))
    query = ReferenceRequest(requestId='rank', dataset='futures-rank-cffex', start='2026-11-03', end='2026-11-03')
    with pytest.raises(CapabilityContractError):
        ranks.read_futures_rank(query, SimpleNamespace(timeout_seconds=60))
    assert calls == ['IF', 'IH', 'IC', 'IM', 'TS', 'TF', 'T', 'TL']
