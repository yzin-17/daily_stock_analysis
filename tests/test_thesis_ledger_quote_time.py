"""报价接口的新鲜度与来源时间回归。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.thesis_ledger_quote_time import quote_time_state


@pytest.mark.parametrize('value,flag,expected', [
    ('2026-09-25T15:00:00+08:00', None, 'stale'),
    ('2026-09-27T06:59:59+00:00', None, 'live'),
    ('2026-09-27T06:50:00+00:00', False, 'live'),
    ('2026-09-27T06:49:59+00:00', False, 'stale'),
    ('2026-09-27T07:00:01+00:00', None, 'unknown'),
    ('2026-09-27T06:59:59', None, 'unknown'),
    (None, None, 'unknown'),
    ('bad', False, 'unknown'),
    ('2026-09-27T06:59:59Z', True, 'stale'),
    (None, True, 'stale'),
])
def test_quote_time_states(value, flag, expected):
    now = datetime(2026, 9, 27, 7, tzinfo=timezone.utc)
    source_time, stale, freshness = quote_time_state(value, flag, now)
    assert freshness == expected
    assert stale == (expected == 'stale')
    if value == '2026-09-27T07:00:01+00:00':
        assert source_time == value


def test_api_serialization_keeps_old_quote_stale_without_manager(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3 as router

    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'quote-time-test-token')
    monkeypatch.delenv('THESIS_LEDGER_FIXTURE_MODE', raising=False)

    quote = SimpleNamespace(
        price=10, open_price=10, high=11, low=9, pre_close=10, volume=100, amount=1000,
        provider_timestamp='2000-01-01T15:00:00+08:00',
        fetched_at=datetime.now(timezone.utc).isoformat(), is_stale=None, source='tencent',
    )
    gateway = SimpleNamespace(quote=lambda *a, **k: SimpleNamespace(
        data=quote, provider='akshare', fallback_used=False,
    ))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway', lambda: gateway)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get('/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                              headers={'Authorization': 'Bearer quote-time-test-token'})
    assert response.status_code == 200
    result = response.json()
    assert result['freshness'] == 'stale'
    assert result['stale'] is True
    assert result['marketTime'].startswith('2000-01-01T07:00:00')
    assert result['upstreamSource'] == 'tencent'


@pytest.mark.parametrize('symbol,source', [
    ('600519.SH', 'a-share-prices-snapshot'),
    ('510300.SH', 'fund-market-snapshot'),
])
def test_hithink_quote_http_preserves_unknown_source_time_and_exact_source(monkeypatch, symbol, source):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3 as router

    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'quote-time-test-token')
    monkeypatch.delenv('THESIS_LEDGER_FIXTURE_MODE', raising=False)
    fetched_at = datetime.now(timezone.utc).isoformat()
    quote = SimpleNamespace(
        price=10, open_price=10, high=10, low=10, pre_close=10,
        volume=1, amount=10, provider_timestamp=None, fetched_at=fetched_at,
        is_stale=None, source=f'hithink/{source}',
        units=(
            {'price_currency': 'CNY', 'volume': 'share', 'turnover_currency': 'CNY'}
            if source == 'a-share-prices-snapshot'
            else {'price_currency': 'CNY', 'volume': 'unknown', 'turnover_currency': 'unknown'}
        ),
    )
    gateway = SimpleNamespace(quote=lambda *a, **k: SimpleNamespace(
        data=quote, provider='hithink', fallback_used=False,
    ))
    monkeypatch.setattr(
        'src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway',
        lambda: gateway,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(
            '/thesis-ledger/market/quote', params={'symbol': symbol},
            headers={'Authorization': 'Bearer quote-time-test-token'},
        )
    assert response.status_code == 200
    result = response.json()
    assert result['symbol'] == symbol
    assert result['provider'] == 'hithink'
    assert result['upstreamSource'] == source
    assert result['marketTime'] is None
    assert result['fetchedAt'] == fetched_at
    assert result['freshness'] == 'unknown'
    assert result['stale'] is False
    assert result['units']['volume'] == (
        'share' if source == 'a-share-prices-snapshot' else 'unknown'
    )

    quote.units = {'price_currency': 'CNY', 'volume': 'share', 'turnover_currency': 'CNY'}
    if source == 'fund-market-snapshot':
        with TestClient(app) as client:
            invalid = client.get(
                '/thesis-ledger/market/quote', params={'symbol': symbol},
                headers={'Authorization': 'Bearer quote-time-test-token'},
            )
        assert invalid.status_code == 502
        assert invalid.json()['detail']['code'] == 'upstream_invalid_response'
