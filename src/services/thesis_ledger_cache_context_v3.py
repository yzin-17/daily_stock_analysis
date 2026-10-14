"""当前缓存控制事实投影；不调用来源、不另建政策或证券分类。"""

from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .thesis_ledger_current_data_route import (
    current_data_route_key, select_current_data_targets, CurrentDataRouteError,
)
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_control import _provider_credential_revision_from_snapshot

CAPABILITIES = {'REALTIME_QUOTE', 'FUND_NAV', 'FUND_NAV_HISTORY', 'FUND_HOLDINGS', 'CHIP_SUMMARY'}


class CacheContextRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    contractVersion: Literal[3]
    consumer: Literal['thesis-ledger']
    requestId: str = Field(min_length=1)
    capability: Literal['REALTIME_QUOTE', 'FUND_NAV', 'FUND_NAV_HISTORY', 'FUND_HOLDINGS', 'CHIP_SUMMARY']
    symbol: str = Field(min_length=1, max_length=64)
    assetType: Literal['STOCK', 'ETF', 'INDEX'] | None = None

    @model_validator(mode='after')
    def parameters(self):
        if self.symbol.strip() != self.symbol or self.symbol.upper() != self.symbol or self.requestId.strip() != self.requestId:
            raise ValueError('缓存上下文身份未规范化')
        if 'assetType' in self.model_fields_set and (self.capability != 'REALTIME_QUOTE' or self.assetType is None):
            raise ValueError('能力不接受报价资产参数')
        return self


class CacheContextError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class CacheRouteKey(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['data']
    market: Literal['CN', 'HK', 'US']
    assetType: Literal['STOCK', 'ETF', 'INDEX', 'MUTUAL_FUND']
    capability: Literal['REALTIME_QUOTE', 'FUND_NAV', 'FUND_NAV_HISTORY', 'FUND_HOLDINGS', 'CHIP_SUMMARY']


class CacheContextResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    contractVersion: Literal[3]
    consumer: Literal['thesis-ledger']
    requestId: str
    symbol: str
    capability: Literal['REALTIME_QUOTE', 'FUND_NAV', 'FUND_NAV_HISTORY', 'FUND_HOLDINGS', 'CHIP_SUMMARY']
    routeKey: CacheRouteKey
    policyRevision: int = Field(ge=0)
    generation: str = Field(pattern=r'^[a-f0-9]{64}$')
    ready: bool
    reason: Literal['disabled', 'not_selected', 'not_eligible'] | None

    @model_validator(mode='after')
    def correlation(self):
        if self.routeKey.capability != self.capability or self.ready == (self.reason is not None):
            raise ValueError('缓存上下文响应关联无效')
        if self.capability.startswith('FUND_') and self.routeKey.assetType != 'MUTUAL_FUND':
            raise ValueError('基金缓存资产类型无效')
        if self.capability == 'CHIP_SUMMARY' and self.routeKey.assetType != 'STOCK':
            raise ValueError('筹码缓存资产类型无效')
        return self


def _sample(runtime, key, symbol):
    policy = runtime.store.effective_policy_v3()
    if (not isinstance(policy, dict) or type(policy.get('revision')) is not int
            or policy['revision'] != policy.get('sourceDesiredRevision') or policy['revision'] < 0
            or type(policy.get('enabled')) is not bool or not isinstance(policy.get('routes'), list)):
        raise CacheContextError('policy_not_applied')
    if any(not isinstance(route, dict) for route in policy['routes']):
        raise CacheContextError('invalid_response')
    matches = [route for route in policy['routes'] if route.get('key') == key]
    if len(matches) > 1:
        raise CacheContextError('invalid_response')
    route = matches[0] if matches else None
    if route and (not isinstance(route.get('targets'), list) or len(route['targets']) > 2):
        raise CacheContextError('invalid_response')
    try:
        select_current_data_targets(policy, capability=key['capability'],
                                    instrument_type=key['assetType'], symbol=symbol)
    except CurrentDataRouteError as error:
        if error.code != 'NO_ELIGIBLE_PROVIDER':
            raise CacheContextError('invalid_response') from None
    registry = runtime.store.provider_registry()
    if not isinstance(registry, list) or any(not isinstance(item, dict) or
            not isinstance(item.get('providerId'), str) for item in registry):
        raise CacheContextError('invalid_response')
    providers = {item['providerId']: item for item in registry}
    if len(providers) != len(registry):
        raise CacheContextError('invalid_response')
    facts = []
    for target in route['targets'] if route else []:
        provider = providers.get(target['providerId'])
        source = CAPABILITY_SOURCES.source(target['providerId'])
        if (provider is None or source is None or type(provider.get('configVersion')) is not int
                or provider['configVersion'] < 0 or type(provider.get('enabled')) is not bool
                or type(provider.get('configured')) is not bool):
            raise CacheContextError('invalid_response')
        requires_credential = source.manifest().get('requiresCredential')
        credential = 'not-required'
        if requires_credential:
            credential = _provider_credential_revision_from_snapshot(runtime.store.provider_credential_snapshot(source.provider_id))
        binding = CAPABILITY_SOURCES.resolve(key, target)
        if binding is None:
            raise CacheContextError('invalid_response')
        facts.append({'target': target, 'configVersion': provider['configVersion'],
                      'enabled': provider.get('enabled'), 'configured': provider.get('configured'),
                      'credentialRevision': credential,
                      'adapterRevision': getattr(binding, 'adapter_revision', None),
                      'sourceRevision': getattr(binding, 'source_revision', None)})
    ready = bool(policy['enabled'] and route and route.get('enabled', True) and any(
        item['target'].get('eligible') is True and item['enabled'] is True and item['configured'] is True
        and item['credentialRevision'] is not None for item in facts))
    reason = None
    if not ready:
        if not policy['enabled'] or route and route.get('enabled') is False:
            reason = 'disabled'
        elif route is None:
            reason = 'not_selected'
        else:
            reason = 'not_eligible'
    return {'policyRevision': policy['revision'], 'sourceDesiredRevision': policy['sourceDesiredRevision'],
            'enabled': policy['enabled'], 'route': route, 'providers': facts, 'ready': ready, 'reason': reason}


def read_cache_context(runtime, payload):
    request = CacheContextRequest.model_validate(payload)
    from .thesis_ledger_provider_runtime import instrument_type_for_symbol
    asset = request.assetType or instrument_type_for_symbol(request.symbol)
    if request.capability.startswith('FUND_'):
        asset = 'MUTUAL_FUND'
    elif request.capability == 'CHIP_SUMMARY':
        asset = 'STOCK'
    key = current_data_route_key(request.capability, asset, request.symbol)
    before = _sample(runtime, key, request.symbol)
    after = _sample(runtime, key, request.symbol)
    if before != after:
        raise CacheContextError('policy_not_applied')
    generation = sha256(json.dumps(before, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    response = {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': request.requestId,
            'symbol': request.symbol, 'capability': request.capability, 'routeKey': key,
            'policyRevision': before['policyRevision'], 'generation': generation,
                'ready': before['ready'], 'reason': before['reason']}
    try:
        return CacheContextResponse.model_validate(response).model_dump()
    except ValidationError:
        raise CacheContextError('invalid_response') from None
