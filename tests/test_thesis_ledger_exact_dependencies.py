"""依赖事实由来源/精确绑定拥有，不以公共供应商特判扩展。"""
from dataclasses import replace

import pytest

from data_provider.thesis_ledger_sources import akshare, baostock
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready


@pytest.mark.parametrize('capability,asset,modules', [
    ('REALTIME_QUOTE', 'ETF', ['requests']), ('REALTIME_QUOTE', 'STOCK', ['akshare']),
    ('DAILY_BAR', 'ETF', ['akshare']),
])
def test_exact_ak_dependencies_preserve_original_path(capability, asset, modules, monkeypatch):
    calls = []
    monkeypatch.setattr('src.services.thesis_ledger_provider_dependencies.find_spec', lambda name: calls.append(name) or object())
    binding = next(b for b in akshare.SOURCE.bindings if b.key.capability == capability and b.key.asset_type == asset)
    if asset == 'ETF' and capability == 'REALTIME_QUOTE': assert binding.dependency_modules == ('requests',)
    assert provider_dependency_ready('akshare', binding.key.route_key(), {'providerId': 'akshare', 'upstreamSource': binding.upstream_source})
    assert calls == modules


def test_source_default_baostock_unknown_and_missing_module(monkeypatch):
    calls = []
    monkeypatch.setattr('src.services.thesis_ledger_provider_dependencies.find_spec', lambda name: calls.append(name) or None)
    assert not provider_dependency_ready('baostock') and calls == ['baostock']
    assert provider_dependency_ready('unknown-isolated-source')
    def failed(name): raise ValueError('模块状态不可用')
    monkeypatch.setattr('src.services.thesis_ledger_provider_dependencies.find_spec', failed)
    assert not provider_dependency_ready('baostock')


def test_new_provider_exact_dependency_needs_only_its_binding(monkeypatch):
    binding = next(b for b in akshare.SOURCE.bindings if b.key.capability == 'REALTIME_QUOTE' and b.key.asset_type == 'ETF')
    binding = replace(binding, dependency_modules=('isolated_transport',))
    source = replace(akshare.SOURCE, provider_id='isolated-source', bindings=(binding,), dependency_modules=('isolated_sdk',), quote_source_aliases=())
    registry = CapabilityRegistry((source,))
    monkeypatch.setattr('src.services.thesis_ledger_provider_dependencies.CAPABILITY_SOURCES', registry)
    calls = []
    monkeypatch.setattr('src.services.thesis_ledger_provider_dependencies.find_spec', lambda name: calls.append(name) or object())
    assert provider_dependency_ready(source.provider_id, binding.key.route_key(), {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source})
    assert calls == ['isolated_transport']
    calls.clear()
    assert provider_dependency_ready(source.provider_id) and calls == ['isolated_sdk']
    for invalid in ('wrong', ('',), (3,), ('x', 'x')):
        with pytest.raises(ValueError): CapabilityRegistry((replace(source, bindings=(replace(binding, dependency_modules=invalid),), quote_source_aliases=()),))
