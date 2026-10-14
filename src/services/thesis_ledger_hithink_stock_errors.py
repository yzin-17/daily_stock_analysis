"""HiThink股票业务错误语义；只输出重建的安全说明。"""

_ERRORS: dict[int, tuple[str, str, bool]] = {
    1001: ("invalid_request", "HiThink 历史行情请求缺少必填参数", False),
    1002: ("invalid_request", "HiThink 历史行情请求参数格式无效", False),
    1003: ("range_exceeded", "HiThink 历史行情请求范围超限", False),
    1004: ("invalid_request", "HiThink 历史行情请求参数冲突", False),
    2001: ("authentication_failed", "HiThink 凭据未通过认证", False),
    2003: ("permission_denied", "HiThink 账号无权访问该行情能力", False),
    3001: ("symbol_not_found", "HiThink 未识别该股票代码", False),
    3002: ("data_not_ready", "HiThink 股票行情数据尚未准备", True),
    3004: ("unsupported_capability", "HiThink 不支持该股票行情能力", False),
    4001: ("rate_limited", "HiThink 行情请求触发限流", True),
    5001: ("upstream_failure", "HiThink 行情服务暂时不可用", True),
    5002: ("upstream_failure", "HiThink 上游行情暂时不可用", True),
    5003: ("upstream_failure", "HiThink 行情服务暂时不可用", True),
}


def classify_hithink_stock_error(
    code: int, message: object, symbol: str,
) -> tuple[str, str, bool] | None:
    """1002的精确未知证券响应独立分类，其余业务码沿用原含义。"""
    if code == 1002 and message == f"Unknown thscode: {symbol}":
        return _ERRORS[3001]
    return _ERRORS.get(code)
