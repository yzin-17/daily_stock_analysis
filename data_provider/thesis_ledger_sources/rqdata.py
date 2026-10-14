"""RQData两精确事件的来源事实与原隔离账号工厂。"""

import re
import json
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot

from src.services.thesis_ledger_capability_registry import EventBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT as CASH_CONTRACT
from src.services.thesis_ledger_event_split_contract_v1 import CONTRACT as SPLIT_CONTRACT
from data_provider.rqdata_event_capability import read_event, wire_project

TARGET = {'providerId': 'rqdata', 'upstreamSource': 'rqdata'}
REVISIONS = {
    'CASH_DISTRIBUTION': {'adapterRevision': 'dsa-rqdata-etf-dividend-identity-v1',
                        'sourceRevision': 'rqdatac-3.7.1-fund-get-dividend-v1'},
    'SPLIT_EVENT': {'adapterRevision': 'dsa-rqdata-etf-split-identity-v1',
                    'sourceRevision': 'rqdatac-3.7.1-fund-get-split-v1'},
}


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest('rqdata', 'RQData', {}, requires_credential=True)


def factory(context):
    from src.services.thesis_ledger_rqdata_read import RqDataClientFactory
    return RqDataClientFactory(context.snapshot.values['username'], context.snapshot.values['password'])


def revisions(capability):
    def resolve(credential_revision):
        if not isinstance(credential_revision, str) or re.fullmatch(r'hmac-sha256-v1:[a-f0-9]{64}', credential_revision) is None:
            return None
        return {**REVISIONS[capability], 'credentialRevision': credential_revision}
    return resolve


def current_admission(store, key, target, provider_manifest):
    from src.services.thesis_ledger_rqdata_event_v3 import current_rqdata_event_admission
    return current_rqdata_event_admission(store, key, target, provider_manifest)


def probe_event(context):
    from src.services.thesis_ledger_rqdata_mapped_read import read_rqdata_mapped_fund_event
    kind = 'split' if context.capability == 'SPLIT_EVENT' else 'dividend'
    read_rqdata_mapped_fund_event(kind, context.symbol, **context.mapped_read_options())


def normalize_credential_value(field, value):
    if field == 'password' and value.strip():
        return value
    return value.strip()


def credential_revision_payload(snapshot: ProviderCredentialSnapshot) -> bytes | None:
    if snapshot.source not in {'environment', 'control'} or snapshot.method != 'username_password':
        return None
    username, password = snapshot.values.get('username'), snapshot.values.get('password')
    if (not isinstance(username, str) or not username.strip() or username != username.strip()
            or not isinstance(password, str) or not password.strip()):
        return None
    payload = b'provider=rqdata\0credential=username-password-v1\0' + json.dumps(
        [username, password], ensure_ascii=True, separators=(',', ':'),
    ).encode('utf-8')
    if snapshot.source == 'control':
        payload = b'source=control\0' + payload
    return payload


SOURCE = SourceDefinition('rqdata', manifest, factory, tuple(
    EventBinding(contract, ExactKey('CN', 'ETF', capability), 'rqdata', read_event,
                 revisions(capability), wire_project, revision_facts=REVISIONS[capability],
                 current_admission=current_admission, probe_read=probe_event)
    for capability, contract in (('CASH_DISTRIBUTION', CASH_CONTRACT), ('SPLIT_EVENT', SPLIT_CONTRACT))
), credentials=(('username_password', ('username', 'password')),),
    normalize_credential_value=normalize_credential_value, credential_revision_payload=credential_revision_payload)
