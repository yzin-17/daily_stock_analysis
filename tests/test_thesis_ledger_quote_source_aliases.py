"""SDK已报告来源只按实际来源声明归一，不补造请求目标。"""
from dataclasses import replace
from enum import Enum

import pytest

from api import thesis_ledger_source_identity as identity
from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.thesis_ledger_capability_registry import CapabilityRegistry


@pytest.mark.parametrize(('provider', 'reported', 'expected'), [
    ('futu', 'futu', 'futu'), ('tencent', 'tencent', 'tencent'), ('tencent', 'akshare_qq', 'tencent'),
    ('akshare', 'akshare_em', 'eastmoney'), ('akshare', 'eastmoney', 'eastmoney'),
    ('akshare', 'akshare_sina', 'sina'), ('akshare', 'sina', 'sina'), ('akshare', 'akshare_qq', 'tencent'),
    ('efinance', 'efinance', 'eastmoney'), ('efinance', 'eastmoney', 'eastmoney'),
    ('hithink', 'hithink/a-share-prices-snapshot', 'a-share-prices-snapshot'),
    ('hithink', 'hithink/fund-market-snapshot', 'fund-market-snapshot'),
])
def test_original_valid_reported_aliases(provider, reported, expected):
    assert identity.upstream_source(reported, provider) == expected
    assert identity.upstream_source('  ' + reported.upper() + ' ', provider) == expected


@pytest.mark.parametrize(('provider', 'reported'), [('hithink', None), ('hithink', 'unknown'),
    ('yfinance', 'yfinance'), ('longbridge', 'longbridge'), ('akshare', 'futu'), ('unregistered', 'tencent')])
def test_unknown_or_wrong_provider_is_not_assigned_source(provider, reported):
    assert identity.upstream_source(reported, provider) is None


def test_enum_is_normalized_using_declared_value():
    class Source(Enum):
        SDK = 'akshare_qq'
    assert identity.upstream_source(Source.SDK, 'tencent') == 'tencent'


def test_new_source_declares_alias_without_public_vendor_branch(monkeypatch):
    source = replace(SOURCE, provider_id='new-source', quote_source_aliases=(('new-sdk-token', 'tencent'),))
    registry = CapabilityRegistry((source,))
    monkeypatch.setattr(identity, 'CAPABILITY_SOURCES', registry)
    assert identity.upstream_source('new-sdk-token', 'new-source') == 'tencent'
    assert identity.upstream_source('tencent', 'new-source') is None


@pytest.mark.parametrize('aliases', [[('sdk', 'tencent')], (('sdk', 'other'),), (('', 'tencent'),),
    (('SDK', 'tencent'),), (('sdk', 'tencent'), ('sdk', 'tencent'))])
def test_registration_rejects_bad_aliases(aliases):
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, quote_source_aliases=aliases),))
