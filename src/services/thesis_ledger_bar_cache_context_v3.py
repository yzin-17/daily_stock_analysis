"""精确K线缓存控制事实：不取价格、不构建适配器、不写入政策。"""

from datetime import datetime, timezone
from hashlib import sha256
import json

from .thesis_ledger_bar_cache_contract import BarCacheRequest
from .thesis_ledger_cache_context_v3 import CacheContextError
from .thesis_ledger_cache_route_epoch import route_cache_epoch
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_control import _provider_credential_revision_from_snapshot
from .thesis_ledger_price_route_access import price_route_admission


def _account_ready(access):
    if access is None:
        return True
    if not isinstance(access, dict):
        raise CacheContextError('invalid_response')
    if access.get('state') != 'allowed' or not access.get('attempted') or not access.get('checkedAt'):
        return False
    expiry = access.get('validUntil')
    if not expiry:
        return access.get('persisted') is True
    try:
        return datetime.fromisoformat(expiry.replace('Z', '+00:00')) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        raise CacheContextError('invalid_response') from None


def _route(policy, key):
    if (not isinstance(policy, dict) or policy.get('contractVersion') != 3
            or type(policy.get('revision')) is not int or policy['revision'] < 0
            or policy['revision'] != policy.get('sourceDesiredRevision')
            or type(policy.get('enabled')) is not bool or not isinstance(policy.get('routes'), list)):
        raise CacheContextError('policy_not_applied')
    if any(not isinstance(route, dict) for route in policy['routes']):
        raise CacheContextError('invalid_response')
    matching = [route for route in policy['routes'] if route.get('key') == key]
    if len(matching) > 1:
        raise CacheContextError('invalid_response')
    route = matching[0] if matching else None
    if route is None:
        return None
    if 'enabled' in route and type(route['enabled']) is not bool:
        raise CacheContextError('invalid_response')
    targets = route.get('targets')
    if not isinstance(targets, list) or len(targets) > 2:
        raise CacheContextError('invalid_response')
    identities = set()
    for index, target in enumerate(targets):
        if (not isinstance(target, dict) or set(target) != {
                'providerId', 'upstreamSource', 'routeIndex', 'eligible', 'reason'}
                or type(target['routeIndex']) is not int or target['routeIndex'] != index
                or type(target['eligible']) is not bool or target['eligible'] != (target['reason'] is None)
                or any(not isinstance(target.get(k), str) or not target[k].strip()
                       or target[k].strip() != target[k] for k in ['providerId', 'upstreamSource'])):
            raise CacheContextError('invalid_response')
        identity = (target['providerId'], target['upstreamSource'])
        if identity in identities:
            raise CacheContextError('invalid_response')
        identities.add(identity)
    return route


def _sample(runtime, request):
    key, pin = request.routeKey.model_dump(), request.routeTarget.model_dump()
    target = {name: pin[name] for name in ['providerId', 'upstreamSource']}
    policy = runtime.store.effective_policy_v3()
    route = _route(policy, key)
    catalog = runtime.market_route_catalog_v3()
    if (not isinstance(catalog, dict) or catalog.get('integrity') != 'complete'
            or not isinstance(catalog.get('entries'), list)
            or any(not isinstance(entry, dict) for entry in catalog['entries'])):
        raise CacheContextError('invalid_response')
    entries = [entry for entry in catalog.get('entries', [])
               if entry.get('key') == key and entry.get('target') == target]
    if len(entries) > 1:
        raise CacheContextError('invalid_response')
    entry = entries[0] if entries else None
    registry = runtime.store.provider_registry()
    if not isinstance(registry, list) or any(not isinstance(item, dict) for item in registry):
        raise CacheContextError('invalid_response')
    providers = [item for item in registry if item.get('providerId') == pin['providerId']]
    if len(providers) != 1:
        raise CacheContextError('invalid_response')
    provider = providers[0]
    if (type(provider.get('configVersion')) is not int or provider['configVersion'] < 0
            or type(provider.get('enabled')) is not bool or type(provider.get('configured')) is not bool):
        raise CacheContextError('invalid_response')
    source = CAPABILITY_SOURCES.source(pin['providerId'])
    binding = CAPABILITY_SOURCES.resolve(key, target)
    credential = 'not-required'
    if source and source.manifest().get('requiresCredential'):
        snapshot = runtime.store.provider_credential_snapshot(source.provider_id)
        if source.credential_revision_kind == 'hmac':
            credential = _provider_credential_revision_from_snapshot(snapshot)
        else:
            version = snapshot.credential_version
            credential = str(version) if type(version) is int and version >= 0 else None
    selected = next((item for item in route['targets'] if all(item.get(k) == v for k, v in pin.items())), None) if route else None
    admission, admission_reason = None, None
    if binding and selected and selected['eligible']:
        from .thesis_ledger_provider_runtime import ThesisLedgerDataRequest
        query = ThesisLedgerDataRequest(request.capability, request.symbol,
            instrument_type=key['assetType'], timeframe=key['timeframe'], adjustment=key['adjustment'],
            start=request.start, end=request.end, parameters={'interactive_chart': True})
        admission, admission_reason = price_route_admission(runtime._current_market_v3_admission, query, key, target)
    ready = bool(policy['enabled'] and route and route.get('enabled', True)
                 and selected and selected['eligible'] and binding and provider['enabled'] and provider['configured']
                 and credential is not None and entry and entry.get('state') == 'ready'
                 and _account_ready(entry.get('accountAccess')) and admission_reason is None)
    reason = None
    if not ready:
        if not policy['enabled'] or route and route.get('enabled') is False:
            reason = 'disabled'
        elif selected is None:
            reason = 'not_selected'
        else:
            reason = 'not_eligible'
    return {'policyRevision': policy['revision'], 'epoch': route_cache_epoch(runtime.store, key, policy['revision']),
            'route': route, 'enabled': policy['enabled'],
            # 首次成功的展示证据不改变读取资格；账号资格等实际控制事实仍完整保留。
            'catalogEntry': {name: value for name, value in entry.items() if name != 'verification'} if entry else None,
            'configVersion': provider['configVersion'], 'providerEnabled': provider['enabled'],
            'configured': provider['configured'], 'credentialRevision': credential,
            'adapterRevision': getattr(binding, 'adapter_revision', None),
            'sourceRevision': getattr(binding, 'source_revision', None), 'admission': admission,
            'ready': ready, 'reason': reason}


def read_bar_cache_context(runtime, payload):
    request = BarCacheRequest.model_validate(payload)
    before = _sample(runtime, request)
    if before != _sample(runtime, request):
        raise CacheContextError('policy_not_applied')
    generation = sha256(json.dumps({'request': request.model_dump(exclude={'requestId'}), 'facts': before},
        sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    return {**request.model_dump(), 'generation': generation, 'policyRevision': before['policyRevision'],
            'ready': before['ready'], 'reason': before['reason']}
