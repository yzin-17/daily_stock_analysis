"""已迁移精确绑定派生修订与目录，不恢复供应商fallback。"""

from copy import deepcopy

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_catalog_manifest_v3 import catalog_manifest_matches_v3
from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions


@pytest.mark.parametrize('key,target,binding', list(CAPABILITY_SOURCES.routes('DAILY_BAR')))
def test_daily_revision_view_preserves_binding_and_credential_identity(key, target, binding):
    source = CAPABILITY_SOURCES.source(target['providerId'])
    manifest = source.manifest()
    revision = 'hmac-sha256-v1:' + 'a' * 64
    actual = market_v3_current_route_revisions(key, target, manifest,
                                             credential_version=7, credential_revision=revision)
    expected_credential = 'not-required'
    if manifest['requiresCredential']:
        expected_credential = revision if source.credential_revision_kind == 'hmac' else '7'
    assert actual == {'adapterRevision': binding.adapter_revision,
                      'sourceRevision': binding.source_revision.strip(), 'credentialRevision': expected_credential}
    assert market_v3_current_route_revisions(key, {**target, 'upstreamSource': 'unregistered'}, manifest,
                                             credential_version=7, credential_revision=revision) is None
    assert market_v3_current_route_revisions(key, target, {**manifest, 'providerId': 'another-provider'},
                                             credential_version=7, credential_revision=revision) is None


@pytest.mark.parametrize('key,target,binding', list(CAPABILITY_SOURCES.routes('REALTIME_QUOTE')))
def test_quote_manifest_requires_exact_binding_and_current_source_declaration(key, target, binding):
    manifest = CAPABILITY_SOURCES.source(target['providerId']).manifest()
    assert catalog_manifest_matches_v3(manifest, key, target)
    removed = deepcopy(manifest)
    removed['upstreamSources'] = [source for source in removed['upstreamSources']
                                 if source['sourceId'] != target['upstreamSource']]
    assert not catalog_manifest_matches_v3(removed, key, target)
    broad = deepcopy(manifest)
    broad['upstreamSources'].append({'sourceId': 'unregistered', 'capabilities': {
        'DAILY_BAR': [key['assetType']], 'REALTIME_QUOTE': [key['assetType']]}})
    assert not catalog_manifest_matches_v3(broad, key, {**target, 'upstreamSource': 'unregistered'})
