"""默认关闭、分别独立准入的官方历史无事件窗口来源。"""

from datetime import datetime, timezone

from data_provider.sse_event_coverage import EventCoverageArchive, ROOT, inventory
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import EventBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT as CASH_CONTRACT
from src.services.thesis_ledger_event_split_contract_v1 import CONTRACT as SPLIT_CONTRACT

PROVIDER = 'official-event-archive'
UPSTREAM = 'sse-disclosure-history'
ADAPTER_REVISION = 'sse-event-absence-v1'
try:
    REVISION = inventory(ROOT)
except (OSError, ValueError):
    REVISION = 'not-loaded'


def revisions(credential_revision=None):
    return {'adapterRevision': ADAPTER_REVISION, 'sourceRevision': 'sse-disclosure-v1:' + REVISION,
            'credentialRevision': 'not-required'}


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    value = build_provider_manifest(PROVIDER, '官方历史事件归档',
        {'CASH_DISTRIBUTION': ('ETF',), 'SPLIT_EVENT': ('ETF',)},
        upstream_sources=((UPSTREAM, '上交所完整公告目录与事件日期'),), markets=('CN',), configuration_mode='built_in')
    value['sourceKind'] = 'local_history'
    for source in value['upstreamSources']:
        source['capabilities'] = {'CASH_DISTRIBUTION': ['ETF'], 'SPLIT_EVENT': ['ETF']}
    return value


def factory(context):
    return EventCoverageArchive(ROOT, REVISION)


def admission_current(context):
    if context.state != 'admitted' or not context.payload:
        return False
    try:
        payload = context.payload
        pack = EventCoverageArchive(ROOT, REVISION).material()
        return (payload.get('evidenceSha256') == REVISION and payload.get('evidenceRef') == 'sha256:' + REVISION
            and all(payload.get(key) == value for key, value in revisions().items())
            and set(payload.get('scopeSymbols', [])) <= set(pack['scopeSymbols'])
            and pack['scopeDateFrom'] <= payload['scopeDateFrom'] <= payload['scopeDateTo'] <= pack['scopeDateTo'])
    except (ValueError, OSError, KeyError, TypeError):
        return False


def read_event(query, context):
    before = context.admitted_state()
    try:
        archive = context.adapter_factory()
        archive.scope(query.symbol, query.start, query.end, query.dataAsOf)
        record = before['admission']
        if (record['evidenceSha256'] != archive.revision or record['evidenceRef'] != 'sha256:' + archive.revision
                or any(record.get(key) != value for key, value in revisions().items())):
            raise ValueError('归档与独立准入不符')
    except (ValueError, OSError, KeyError, TypeError):
        raise CapabilityContractError('invalid_response', '官方事件归档、窗口或原文验证失败') from None
    after = context.admitted_state()
    if after != before:
        raise CapabilityContractError('policy_not_applied', '事件读取期间控制或准入变化')
    fetched = datetime.now(timezone.utc)
    admission = event_admission_snapshot(after['admission'], observed_at=fetched)
    return {**query.model_dump(), 'fetchedAt': fetched.isoformat(), 'providerRevision': revisions()['sourceRevision'],
        'admission': admission, 'facts': [],
        'coverage': {'complete': True, 'admissionEvidenceRef': admission['evidenceRef']}}


def wire_project(result):
    return result.model_dump(mode='json', exclude_unset=True)


def probe(context):
    pack = EventCoverageArchive(ROOT, REVISION).material()
    return {'checkedSymbols': len(pack['scopeSymbols']), 'scopeDateFrom': pack['scopeDateFrom'],
            'scopeDateTo': pack['scopeDateTo'], 'sourceRevision': revisions()['sourceRevision']}


SOURCE = SourceDefinition(PROVIDER, manifest, factory, tuple(
    EventBinding(contract, ExactKey('CN', 'ETF', capability), UPSTREAM,
        read_event, revisions, wire_project)
    for capability, contract in [('CASH_DISTRIBUTION', CASH_CONTRACT), ('SPLIT_EVENT', SPLIT_CONTRACT)]
), default_enabled=False, dependency_modules=('pypdf',), policy_admission_guard=admission_current,
    exact_probe_read=probe)
