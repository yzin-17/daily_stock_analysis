"""宽manifest现行例外来自来源精确绑定，不新增中央资产表。"""

from dataclasses import replace

import pytest

from src.services.provider_manifest import build_provider_manifest
from src.services.thesis_ledger_capability_registry import DailyBinding
from data_provider.thesis_ledger_sources import akshare


@pytest.mark.parametrize('provider', ['akshare', 'new-provider'])
def test_generic_builder_uses_supplied_declaration_without_vendor_exception(provider):
    manifest = build_provider_manifest(provider, '隔离来源', {'DAILY_BAR': ('STOCK', 'ETF')},
                                       upstream_sources=(('sina', '隔离上游'),))
    assert manifest['upstreamSources'][0]['capabilities']['DAILY_BAR'] == ['ETF', 'STOCK']


def test_production_sina_scope_stays_stock_only():
    value = next(item for item in akshare.SOURCE.manifest()['upstreamSources'] if item['sourceId'] == 'sina')
    assert value['capabilities']['DAILY_BAR'] == ['STOCK']
    assert value['capabilities']['REALTIME_QUOTE'] == ['STOCK']
    assert value['capabilities']['DAILY_BAR'] == sorted({binding.key.asset_type for binding in akshare.SOURCE.bindings
        if isinstance(binding, DailyBinding) and binding.upstream_source == 'sina'})


def test_source_manifest_derived_view_tracks_only_its_binding(monkeypatch):
    original = akshare.SOURCE
    binding = next(binding for binding in original.bindings if isinstance(binding, DailyBinding)
                   and binding.upstream_source == 'sina')
    replacement = replace(binding, key=replace(binding.key, asset_type='ETF'))
    others = tuple(binding for binding in original.bindings if not (
        isinstance(binding, DailyBinding) and binding.upstream_source == 'sina'))
    monkeypatch.setattr(akshare, 'SOURCE', replace(original, bindings=others + (replacement,)))
    manifest = akshare.SOURCE.manifest()
    sources = {item['sourceId']: item for item in manifest['upstreamSources']}
    assert sources['sina']['capabilities']['DAILY_BAR'] == ['ETF']
    # 其他旧宽声明保持，不能借本片修改腾讯潜在声明或新增精确执行。
    assert sources['tencent']['capabilities']['DAILY_BAR'] == ['ETF', 'STOCK']
    assert sources['eastmoney']['capabilities']['REALTIME_QUOTE'] == ['STOCK', 'ETF']
    assert sources['sina']['capabilities']['REALTIME_QUOTE'] == ['STOCK']
