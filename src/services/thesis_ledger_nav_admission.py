"""NAV 精确策略、目录与准入的读取前后守卫。"""

from copy import deepcopy
from datetime import datetime, timezone

from src.services.thesis_ledger_current_data_route import select_current_data_targets, CurrentDataRouteError
from src.services.thesis_ledger_nav_request import NAV_TARGET, NAV_ADAPTER_REVISION, NAV_SOURCE_REVISION, NavProductionError
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_nav_rules import _instant


def admitted_nav_state(runtime, request, *, first=None, last=None):
    policy = runtime.store.effective_policy_v3()
    try:
        selected = select_current_data_targets(policy, capability='FUND_NAV_HISTORY',
                                               instrument_type='MUTUAL_FUND', symbol=request['symbol'])
    except CurrentDataRouteError:
        raise NavProductionError('policy_not_applied') from None
    if (policy['revision'] != request['effectivePolicyRevision']
            or policy['sourceDesiredRevision'] != request['desiredRevision']):
        raise NavProductionError('policy_not_applied')
    target = request['routeTarget']
    if (target['providerId'], target['upstreamSource'], target['routeIndex']) not in selected:
        raise NavProductionError('not_admitted')
    catalog = runtime.market_route_catalog_v3()
    if (not isinstance(catalog, dict) or catalog.get('integrity') != 'complete'
            or type(catalog.get('catalogRevision')) is not int
            or catalog['catalogRevision'] != request['catalogRevision']):
        raise NavProductionError('policy_not_applied')
    entries = [entry for entry in catalog.get('entries', []) if isinstance(entry, dict)
               and entry.get('key') == request['routeKey'] and entry.get('target') == NAV_TARGET]
    if len(entries) != 1 or entries[0].get('state') != 'ready':
        raise NavProductionError('not_admitted')
    admission = runtime._current_market_v3_admission(request['routeKey'], NAV_TARGET)
    if not isinstance(admission, dict) or not route_admission_scope_applies(
        admission, symbol=request['symbol'], date_from=first or request['start'], date_to=last or request['end'],
    ):
        raise NavProductionError('not_admitted')
    expected = {'consumer': 'thesis-ledger', 'routeKey': request['routeKey'], 'target': NAV_TARGET,
                'status': 'admitted', 'admissionState': 'admitted', 'adapterRevision': NAV_ADAPTER_REVISION,
                'sourceRevision': NAV_SOURCE_REVISION, 'credentialRevision': 'not-required',
                'invalidatedAt': None, 'invalidationReason': None}
    if any(admission.get(key) != value for key, value in expected.items()):
        raise NavProductionError('not_admitted')
    try:
        now = datetime.now(timezone.utc)
        if not (_instant(admission['validFrom']) <= now < _instant(admission['validUntil'])
                and _instant(admission['recordedAt']) <= now):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise NavProductionError('not_admitted') from None
    return deepcopy({'policy': {key: value for key, value in policy.items() if key != 'appliedAt'},
                     'catalog': {key: catalog[key] for key in ('catalogRevision', 'integrity', 'entries')},
                     'admission': admission})


def nav_admission_snapshot(admission):
    fields = ['consumer', 'routeKey', 'target', 'status', 'admissionState', 'evidenceRef', 'evidenceSha256',
              'scopeSymbols', 'scopeDateFrom', 'scopeDateTo', 'adapterRevision', 'sourceRevision',
              'credentialRevision', 'validFrom', 'validUntil', 'recordVersion', 'recordedAt',
              'invalidatedAt', 'invalidationReason']
    if any(key not in admission for key in fields):
        raise NavProductionError('invalid_response')
    return {key: deepcopy(admission[key]) for key in fields}
