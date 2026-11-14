"""来源错误的安全分类；只输出稳定类别，不透传供应商原文。"""


def classify_provider_failure(error):
    declared = getattr(error, 'code', None) or getattr(error, 'error_kind', None)
    if declared == 'timeout':
        return 'network_failure'
    known = {'rate_limited', 'rate_limited_global', 'quota_unavailable', 'authentication_failed',
             'permission_denied', 'timeout', 'invalid_response', 'not_covered'}
    if declared in known:
        return declared
    status = getattr(error, 'status_code', None)
    if status is None:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
    if status == 429:
        return 'rate_limited'
    if status == 401:
        return 'authentication_failed'
    if status == 403:
        return 'permission_denied'
    if isinstance(status, int) and status >= 500:
        return 'upstream_failure'
    text = str(error).lower()
    if any(part in text for part in ('每天最多', '每日额度', 'daily limit', 'quota', '额度不足', '额度用尽', '历史额度')):
        return 'quota_unavailable'
    if any(part in text for part in ('429', 'rate limit', 'too many request', '请求过于频繁', '频率', '每分钟最多')):
        return 'rate_limited'
    if any(part in text for part in ('401', 'unauthor', 'invalid api key', 'invalid token', 'token无效', '认证失败')):
        return 'authentication_failed'
    if any(part in text for part in ('403', 'forbidden', 'permission', 'access denied', '权限', '积分不足', 'premium')):
        return 'permission_denied'
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return 'network_failure'
    return 'upstream_failure'
