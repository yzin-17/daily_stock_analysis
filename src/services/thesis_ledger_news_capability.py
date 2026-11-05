"""新闻合同接入现有精确绑定接口；不登记来源、不装配运行路由。"""

from .thesis_ledger_news_contract_v1 import NEWS_CAPABILITY, NewsRequest, NewsResponse


def _reject(message, code="invalid_response"):
    from .thesis_ledger_capability_contracts import CapabilityContractError
    raise CapabilityContractError(code, message)


def _binding(request: NewsRequest, key, _target):
    if key.get("market") != request.instrument.market or key.get("assetType") != request.instrument.assetType or key.get("capability") != NEWS_CAPABILITY:
        _reject("新闻请求必须匹配规范标的与精确能力键", "invalid_request")


def _correlate(request: NewsRequest, result: NewsResponse, target):
    if result.requestId != request.requestId or result.instrument != request.instrument:
        _reject("新闻响应与本次请求标识或标的不一致")
    if result.coverage.publishedStart != request.publishedStart or result.coverage.publishedEnd != request.publishedEnd:
        _reject("新闻响应窗口与本次请求不一致")
    if len(result.items) > request.limit:
        _reject("新闻响应超出请求条数上限")
    if result.route and (result.route.providerId != target.get("providerId") or result.route.upstreamSource != target.get("upstreamSource")):
        _reject("新闻实际来源与精确绑定目标不一致")
    return result


def news_capability_contract():
    """沿用 CapabilityContract；DTO 可在不加载旧配置与模型 SDK 时独立导入。"""
    from .thesis_ledger_capability_registry import CapabilityContract
    return CapabilityContract(NEWS_CAPABILITY, NewsRequest, NewsResponse.model_validate,
                              validate_binding=_binding, correlate_result=_correlate)
