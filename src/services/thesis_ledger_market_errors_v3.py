"""日线与图表共用安全错误投影；不暴露供应商原文或凭据。"""

MARKET_ERROR_MESSAGES = {
    "unsupported_capability": "来源不支持请求的数据能力",
    "unsupported_data_contract_version": "不支持请求的数据契约版本",
    "unsupported_price_basis": "来源不支持请求的价格口径",
    "insufficient_coverage": "请求区间的数据覆盖不足",
    "upstream_failure": "行情上游暂时不可用",
    "invalid_response": "行情上游响应格式无效",
    "source_symbol_unrecognized": "来源未识别请求的证券",
    "invalid_request": "来源拒绝请求参数",
    "permission_denied": "来源权限不足",
    "authentication_failed": "来源凭据未通过认证",
}

_RUNTIME_ERRORS = {
    "unsupported_adjustment": ("unsupported_price_basis", 422),
    "not_covered": ("insufficient_coverage", 422),
    "insufficient_coverage": ("insufficient_coverage", 422),
    "invalid_response": ("invalid_response", 502),
    "upstream_invalid_response": ("invalid_response", 502),
    "symbol_not_found": ("source_symbol_unrecognized", 404),
    "invalid_request": ("invalid_request", 422),
    "permission_denied": ("permission_denied", 403),
    "authentication_failed": ("authentication_failed", 403),
}


def market_runtime_error(error):
    return _RUNTIME_ERRORS.get(str(getattr(error, "code", "")), ("upstream_failure", 503))
