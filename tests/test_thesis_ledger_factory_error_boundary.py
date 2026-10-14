"""工厂作用域不能改写标准失败；不暴露源原文或凭据诊断。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerProviderRuntime
from src.services.thesis_ledger_capability_contracts import ReadContext
from src.services.thesis_ledger_factory_execution import execute_source_factory


@pytest.mark.parametrize('scope', ['route', 'provider', 'unscoped'])
@pytest.mark.parametrize('code,retryable', [('permission_denied', False), ('invalid_request', False), ('not_covered', True)])
def test_standard_factory_failure_is_scope_independent_and_safe(monkeypatch, scope, code, retryable):
    provider = 'isolated-factory-errors'
    calls, reads = [], []
    binding = next(binding for binding in SOURCE.bindings if binding.key.capability == 'DAILY_BAR')
    def factory(context):
        calls.append(context)
        raise CapabilityContractError(code, 'secret-access-token', retryable=retryable,
                                      diagnostics={'token': 'secret-access-token'})
    source = replace(SOURCE, provider_id=provider, bindings=(binding,), quote_source_aliases=(),
                     adapter_import=None, factory=factory,
                     factory_scope='provider' if scope == 'provider' else 'route',
                     unscoped_factory=factory if scope == 'unscoped' else None)
    registry = CapabilityRegistry((source,))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    monkeypatch.setitem(PROVIDER_MANIFESTS, provider, {'requiresCredential': False})
    snapshot = ProviderCredentialSnapshot.create(provider, 'environment', 'api_key', {},
                                                config_version=0, credential_version=0)
    runtime = ThesisLedgerProviderRuntime(SimpleNamespace(
        provider_credential_snapshot=lambda item: reads.append(item) or snapshot))
    with pytest.raises(ProviderCallError) as error:
        runtime._adapter(provider, market_v3_route_key=None if scope == 'unscoped' else binding.key.route_key())
    assert error.value.code == code and error.value.retryable is retryable
    assert str(error.value) == 'Provider 适配器未就绪' and error.value.diagnostics == {}
    assert 'secret-access-token' not in str(error.value.detail())
    assert len(calls) == 1 and calls[0].adapter is None
    assert not hasattr(calls[0], 'runtime')
    assert reads == ([provider] if scope == 'provider' else [])
    assert runtime.adapters == {} and runtime._adapter_versions == {}


def test_success_keeps_exact_context_value_and_single_invocation():
    calls = []
    context, value = ReadContext(None, timeout_seconds=4.5), object()
    assert execute_source_factory(lambda actual: calls.append(actual) or value, ProviderCallError, context) is value
    assert calls == [context]


@pytest.mark.parametrize('error', [ValueError('original'), ProviderCallError('not_admitted', 'original')])
def test_other_errors_keep_original_boundary_and_identity(error):
    def factory(_):
        raise error
    with pytest.raises(type(error)) as result:
        execute_source_factory(factory, ProviderCallError, ReadContext(None))
    assert result.value is error
