"""目录本地就绪沿精确来源声明，不维护供应商文件选择表。"""

from dataclasses import replace

import pytest

from src.services import thesis_ledger_local_rules as ready
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, SourceDefinition
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


def local_binding():
    return next(binding for key, target, binding in CAPABILITY_SOURCES.routes()
                if target['providerId'] == 'official-rules')


@pytest.mark.parametrize('value', [True, False, None])
def test_new_source_can_own_readiness_without_public_branch(monkeypatch, value):
    binding = local_binding()
    calls = []
    def policy(context):
        calls.append(context)
        assert context.key == binding.key.route_key()
        assert context.target == {'providerId': 'readiness-fixture', 'upstreamSource': binding.upstream_source}
        assert not hasattr(context, 'runtime') and not hasattr(context, 'adapter')
        return value
    source = SourceDefinition('readiness-fixture', lambda: {}, lambda _: pytest.fail('不得创建工厂'),
                              (binding,), catalog_readiness=policy)
    monkeypatch.setattr(ready, 'CAPABILITY_SOURCES', CapabilityRegistry((source,)))
    target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}
    assert ready.local_rule_source_ready(binding.key.route_key(), target) is value
    assert len(calls) == 1
    assert ready.local_rule_source_ready(binding.key.route_key(), {**target, 'upstreamSource': 'wrong'}) is None
    assert len(calls) == 1


@pytest.mark.parametrize('value', [1, 'ready', {}, []])
def test_invalid_readiness_cannot_be_treated_as_available(monkeypatch, value):
    binding = local_binding()
    source = SourceDefinition('readiness-fixture', lambda: {}, lambda _: None, (binding,), catalog_readiness=lambda _: value)
    monkeypatch.setattr(ready, 'CAPABILITY_SOURCES', CapabilityRegistry((source,)))
    with pytest.raises(ValueError):
        ready.local_rule_source_ready(binding.key.route_key(), {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source})


def test_invalid_policy_rejected_before_factory():
    source = SourceDefinition('readiness-fixture', lambda: {}, lambda _: pytest.fail('不得创建工厂'),
                              (local_binding(),), catalog_readiness=1)
    with pytest.raises(ValueError):
        CapabilityRegistry((source,))


@pytest.mark.parametrize('provider', ['official-rules', 'official-hk-rules', 'official-us-rules'])
def test_original_file_missing_is_false_without_reading_data(monkeypatch, tmp_path, provider):
    source = CAPABILITY_SOURCES.source(provider)
    binding = next(binding for binding in source.bindings if binding.key.capability == 'HISTORICAL_PUBLIC_FEES')
    target = {'providerId': provider, 'upstreamSource': binding.upstream_source}
    if provider == 'official-rules':
        from src.services import thesis_ledger_public_fees as producer
        monkeypatch.setattr(producer, 'DATA_PATH', tmp_path / 'missing.json')
    else:
        import importlib
        module = importlib.import_module(source.factory.__module__)
        monkeypatch.setattr(module, 'multimarket_rule_path', lambda *_: tmp_path / 'missing.json')
    assert ready.local_rule_source_ready(binding.key.route_key(), target) is False


@pytest.mark.parametrize('has_records', [False, True])
def test_original_announcement_library_fallback_stays_read_only(monkeypatch, tmp_path, has_records):
    from src.services import thesis_ledger_trading_status as producer
    from src.services.thesis_ledger_announcements import AnnouncementRepository
    source = CAPABILITY_SOURCES.source('official-states')
    binding = next(binding for binding in source.bindings if binding.key.capability == 'HISTORICAL_TRADING_STATUS'
                   and binding.upstream_source == producer.UPSTREAM_SOURCE)
    reads = []
    monkeypatch.setattr(producer, 'DATA_PATH', tmp_path / 'missing.json')
    monkeypatch.setattr(AnnouncementRepository, 'records', lambda _: reads.append('records') or ([{}] if has_records else []))
    assert ready.local_rule_source_ready(binding.key.route_key(), {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}) is has_records
    assert reads == ['records']


def test_independent_notice_capabilities_use_their_exact_source_readiness(monkeypatch):
    from src.services import thesis_ledger_trading_status as producer

    source = CAPABILITY_SOURCES.source('official-states')
    bindings = [binding for binding in source.bindings if binding.key.capability != 'HISTORICAL_TRADING_STATUS']
    checked = []
    monkeypatch.setattr(producer, 'trading_status_source_ready',
                        lambda target: checked.append(target) or target['upstreamSource'] == producer.RISK_UPSTREAM_SOURCE)
    for binding in bindings:
        target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}
        assert not ready.local_rule_route_matches(binding.key.route_key(), target)
        assert ready.local_rule_source_ready(binding.key.route_key(), target) is (
            binding.upstream_source == producer.RISK_UPSTREAM_SOURCE
        )
    assert [target['upstreamSource'] for target in checked] == [binding.upstream_source for binding in bindings]
    assert checked


def test_recommendation_metadata_is_source_owned_and_optional():
    from src.services.thesis_ledger_source_recommendation import source_recommendation
    source = CAPABILITY_SOURCES.source('akshare')
    binding = next(item for item in source.bindings if item.upstream_source == 'eastmoney')
    key, target = binding.key.route_key(), {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
    value = source_recommendation(CAPABILITY_SOURCES, key, target)
    assert value['upstreamGroup'] == 'eastmoney'
    assert 'priority' not in value and 'readEvidence' not in value
    assert 'readEvidence' not in source_recommendation(CAPABILITY_SOURCES, key, target)
    binding = next(item for item in source.bindings if getattr(item, 'admission_required', False) is True)
    value = source_recommendation(CAPABILITY_SOURCES, binding.key.route_key(),
                                 {'providerId': 'akshare', 'upstreamSource': binding.upstream_source},
                                 read_evidence=True)
    assert value['readEvidence'] is True
    assert source_recommendation(CAPABILITY_SOURCES, key, {**target, 'upstreamSource': 'missing'}) is None
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(source, upstream_groups=(('eastmoney', 'one'), ('eastmoney', 'two'))),))
