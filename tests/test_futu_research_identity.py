"""富途归一化研究必须有来源身份和日历，不能借用历史按股资格。"""

from datetime import datetime, timezone
import json

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_dependency_facts import DependencyFactError, instrument_facts_response
from src.services.thesis_ledger_reference_identity import validate_instrument_request
from src.services.thesis_ledger_research_calendar import research_market_coverage_context


TARGET = {'providerId': 'futu', 'upstreamSource': 'futu', 'routeIndex': 0}
AFTER_OBSERVATION = datetime(2026, 10, 17, tzinfo=timezone.utc)


def key(market='HK', asset='STOCK', adjustment='qfq'):
    return {'kind': 'bar', 'market': market, 'assetType': asset,
            'capability': 'DAILY_BAR', 'timeframe': '1d', 'adjustment': adjustment}


@pytest.mark.parametrize('symbol,market,currency', [
    ('00700.HK', 'HK', 'HKD'), ('AAPL.US', 'US', 'USD'),
])
@pytest.mark.parametrize('adjustment', ['none', 'qfq', 'hfq'])
def test_observed_stock_has_current_calendar_and_unknown_historical_units(symbol, market, currency, adjustment):
    route = key(market, adjustment=adjustment)
    validate_instrument_request(symbol, market, 'STOCK', lambda value: value,
                                identity_only=True, adjustment=adjustment,
                                bar_provider_id='futu', bar_upstream_source='futu')
    context = research_market_coverage_context({
        'symbol': symbol, 'routeKey': route, 'routeTarget': TARGET,
        'tradabilityMode': 'assume-untradable-no-bar',
        'start': '2026-08-22', 'end': '2026-10-08',
    }, observed_at=AFTER_OBSERVATION)
    assert context is not None and context['expectedPostListingSessionDates']
    result = instrument_facts_response(
        symbol, AFTER_OBSERVATION, '2026-08-22', '2026-10-08',
        '2026-09-15', '2026-10-08', [], instrument_type='STOCK', market=market,
        identity_only=True, route_key=route, route_target=TARGET,
    )
    assert result['status'] == 'supported' and result['provider'] == 'futu'
    fact, = result['facts']
    assert fact['symbol'] == symbol and fact['currency'] == currency
    assert fact['lotSize'] is None and fact['tickSize'] is None
    assert fact['tradable'] is False and fact['executionRules']['status'] == 'unavailable'
    assert fact['providerRevision'].startswith('futu-current-identity-sha256:')


@pytest.mark.parametrize('symbol,market,asset,adjustment', [
    ('00147.HK', 'HK', 'STOCK', 'qfq'),
    ('00147.HK', 'HK', 'STOCK', 'hfq'),
    ('00147.HK', 'HK', 'STOCK', 'none'),
    ('02800.HK', 'HK', 'ETF', 'none'),
    ('00700.HK', 'US', 'STOCK', 'none'),
    ('02800.HK', 'HK', 'ETF', 'qfq'),
    ('SPY.US', 'US', 'ETF', 'qfq'),
    ('SPY.US', 'US', 'ETF', 'hfq'),
    ('00700.HK', 'US', 'STOCK', 'qfq'),
    ('00700.HK', 'US', 'STOCK', 'hfq'),
])
def test_other_scope_is_not_qualified_by_observed_prices(symbol, market, asset, adjustment):
    with pytest.raises(DependencyFactError):
        validate_instrument_request(symbol, market, asset, lambda value: value,
                                    identity_only=True, adjustment=adjustment,
                                    bar_provider_id='futu', bar_upstream_source='futu')


@pytest.mark.parametrize('adjustment', ['none', 'qfq', 'hfq'])
def test_observation_cannot_be_used_before_its_source_clock(adjustment):
    before = datetime(2026, 10, 16, 9, 45, tzinfo=timezone.utc)
    with pytest.raises(DependencyFactError):
        instrument_facts_response(
            '00700.HK', before, '2026-08-22', '2026-10-08',
            '2026-09-15', '2026-10-08', [], instrument_type='STOCK', market='HK',
            identity_only=True, route_key=key(adjustment=adjustment), route_target=TARGET,
        )


def test_calendar_opt_in_does_not_apply_to_other_assets():
    for route in [key(asset='ETF', adjustment=adjustment) for adjustment in ('none', 'qfq', 'hfq')]:
        binding = CAPABILITY_SOURCES.resolve(route, TARGET)
        assert binding.research_calendar_eligible is None
        assert binding.research_instrument_policy is None


@pytest.mark.parametrize('symbol,market', [('00700.HK', 'HK'), ('AAPL.US', 'US')])
def test_raw_reference_does_not_grant_strict_coverage_or_actual_share_execution(symbol, market):
    request = {'symbol': symbol, 'routeKey': key(market, adjustment='none'),
               'routeTarget': TARGET, 'start': '2026-08-22', 'end': '2026-10-08'}
    assert research_market_coverage_context(request, observed_at=AFTER_OBSERVATION) is None
    with pytest.raises(DependencyFactError):
        validate_instrument_request(symbol, market, 'STOCK', lambda value: value,
                                    identity_only=False, adjustment='none',
                                    bar_provider_id='futu', bar_upstream_source='futu')


@pytest.mark.parametrize('adjustment', ['none', 'qfq', 'hfq'])
@pytest.mark.parametrize('mutation', ['currency', 'timezone', 'symbol', 'asset', 'exchange', 'clock', 'receipt', 'duplicate'])
def test_unverifiable_or_conflicting_identity_does_not_qualify(monkeypatch, tmp_path, mutation, adjustment):
    from data_provider import futu_research_identity as identity

    pack = json.loads(identity.DATA_PATH.read_text())
    row = pack['identities'][0]
    if mutation == 'currency':
        row['identity']['metadata']['currency'] = 'USD'
    elif mutation == 'timezone':
        row['identity']['metadata']['exchangeTimezoneName'] = 'UTC'
    elif mutation == 'symbol':
        row['identity']['metadata']['symbol'] = '0147.HK'
    elif mutation == 'asset':
        row['futu']['assetType'] = 'ETF'
    elif mutation == 'exchange':
        row['futu']['exchange'] = 'US_NASDAQ'
    elif mutation == 'clock':
        row['futu']['observedAt'] = '2027-01-01T00:00:00Z'
    elif mutation == 'receipt':
        row['identityReceiptSha256'] = 'unknown'
    else:
        pack['identities'].append(dict(row))
    path = tmp_path / 'conflicting-identities.json'
    path.write_text(json.dumps(pack))
    monkeypatch.setattr(identity, 'DATA_PATH', path)
    assert identity.supports_symbol('00700.HK', 'HK') is False
    with pytest.raises(DependencyFactError):
        instrument_facts_response(
            '00700.HK', AFTER_OBSERVATION, '2026-08-22', '2026-10-08',
            '2026-09-15', '2026-10-08', [], instrument_type='STOCK', market='HK',
            identity_only=True, route_key=key(adjustment=adjustment), route_target=TARGET,
        )
