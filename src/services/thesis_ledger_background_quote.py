"""已选来源的后台报价计划；只放行公开腾讯ETF，不改变路由或预算。"""
import re
from .thesis_ledger_cache_context_v3 import CacheContextRequest, read_cache_context
from .thesis_ledger_current_data_route import select_current_data_targets, CurrentDataRouteError


def background_quote_plan(runtime, payload):
    request = CacheContextRequest.model_validate({**payload, 'capability': 'REALTIME_QUOTE'})
    if set(payload) != {'contractVersion', 'consumer', 'requestId', 'symbol', 'assetType'}:
        raise ValueError('后台报价计划参数无效')
    context = read_cache_context(runtime, request.model_dump(exclude_none=True))
    target, reason = None, context['reason']
    if context['ready']:
        policy = runtime.store.effective_policy_v3()
        targets = select_current_data_targets(policy, capability='REALTIME_QUOTE',
                    instrument_type=request.assetType, symbol=request.symbol)
        if policy['revision'] != context['policyRevision']:
            raise CurrentDataRouteError('not_admitted', '后台计划期间策略已变化')
        first = targets[0]
        target = {'providerId': first[0], 'upstreamSource': first[1], 'routeIndex': first[2]}
        key = context['routeKey']
        if (key['market'] != 'CN' or key['assetType'] != 'ETF'
                or not re.fullmatch(r'\d{6}\.(SH|SZ)', request.symbol)
                or first[:2] != ('tencent', 'tencent')):
            reason = 'background_budget_unverified'
    allowed = context['ready'] and reason is None
    return {**payload, 'generation': context['generation'], 'policyRevision': context['policyRevision'],
            'acquisition': 'public-snapshot' if allowed else 'blocked', 'reason': reason,
            'target': target, 'minIntervalSeconds': 600 if allowed else None,
            'sourceUpdateIntervalSeconds': None}


def constrain_background_quote(runtime, request, targets):
    pin = (request.parameters or {}).get('backgroundQuote')
    if pin is None:
        return targets
    from .thesis_ledger_provider_runtime import ProviderCallError
    if not isinstance(pin, dict) or set(pin) != {'generation'}:
        raise ProviderCallError('invalid_request', '后台报价执行身份无效')
    plan = background_quote_plan(runtime, {'contractVersion': 1, 'consumer': 'thesis-ledger',
        'requestId': request.request_id, 'symbol': request.symbol, 'assetType': request.instrument_type})
    if plan['acquisition'] != 'public-snapshot' or plan['generation'] != pin['generation']:
        raise ProviderCallError('not_admitted', '后台报价来源或控制版本已变化')
    target = plan['target']
    expected = (target['providerId'], target['upstreamSource'], target['routeIndex'])
    if not targets or targets[0] != expected:
        raise ProviderCallError('not_admitted', '后台报价不能跳过当前来源顺序')
    return [expected]


def read_quote_gateway(symbol, request_id, asset_type, background=None):
    from .thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway
    options = {'request_id': request_id}
    if asset_type is not None:
        options['instrument_type'] = asset_type
    if background is not None:
        options['parameters'] = {'backgroundQuote': background}
    return get_thesis_ledger_data_gateway().quote(symbol, **options)
