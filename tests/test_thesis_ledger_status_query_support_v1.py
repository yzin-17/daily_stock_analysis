"""参数资格与来源实际前置校验一致；静态目录不能宣称请求窗口完整。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_status_query_support_v1 import SUPPORT
from src.services.thesis_ledger_route_catalog import build_market_route_catalog_v3
from src.services.thesis_ledger_provider_manifests import PROVIDER_MANIFESTS

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/market-status-capabilities-v1.shared.json').read_text())['querySupport']


@pytest.mark.parametrize('support', FIXTURES)
def test_shared_parameter_qualification(support):
    assert SUPPORT.validate_python(support).model_dump() == support


@pytest.mark.parametrize('support', [
    {'capability': 'RISK_WARNING', 'detailKinds': ['coarseST'], 'riskCategories': ['delisting']},
    {'capability': 'RISK_WARNING', 'detailKinds': ['riskCategories'], 'riskCategories': []},
    {'capability': 'DAILY_PRICE_RULE', 'representations': ['rule', 'rule']},
    {'capability': 'SECURITY_BASIC_DATES', 'fields': ['ipoDate', 'ipoDate']},
    {'capability': 'LISTING_PHASE', 'phases': ['unknown']},
    {'capability': 'DAILY_TRADING_STATUS', 'granularity': 'day', 'maximumWindowDays': True},
])
def test_qualification_rejects_ambiguity(support):
    with pytest.raises(ValidationError):
        SUPPORT.validate_python(support)


def test_catalog_and_binding_have_one_parameter_fact_source(monkeypatch):
    monkeypatch.setattr('src.services.thesis_ledger_route_catalog.provider_dependency_ready', lambda *args: True)
    providers = [{**manifest, 'enabled': True, 'configured': True, 'requiresCredential': False}
                 for manifest in PROVIDER_MANIFESTS.values()]
    catalog = build_market_route_catalog_v3(providers, lambda *args: True)
    projected = [entry for entry in catalog['entries'] if 'querySupport' in entry]
    assert len(projected) == 10
    for entry in projected:
        binding = CAPABILITY_SOURCES.resolve(entry['key'], entry['target'])
        assert entry['querySupport'] == binding.query_support.model_dump()
        assert 'complete' not in entry['querySupport']
    coarse = next(entry for entry in projected if entry['target']['providerId'] == 'baostock' and entry['key']['capability'] == 'RISK_WARNING')
    assert coarse['querySupport']['detailKinds'] == ['coarseST']
    assert coarse['querySupport']['riskCategories'] == []
    assert all('querySupport' not in entry for entry in catalog['entries'] if entry['key']['kind'] == 'bar')
