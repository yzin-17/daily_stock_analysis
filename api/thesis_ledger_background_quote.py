"""后台计划只读；执行只允许原顺序中的已选公开来源。"""
from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError
from api.thesis_ledger_auth import require_contract_token, _error
from src.services.thesis_ledger_background_quote import background_quote_plan
from src.services.thesis_ledger_current_data_route import CurrentDataRouteError
from src.services.thesis_ledger_cache_context_v3 import CacheContextError


def build_background_quote_router(get_runtime):
    router = APIRouter(dependencies=[Depends(require_contract_token)])

    def plan(payload):
        try:
            return background_quote_plan(get_runtime(), payload)
        except (CurrentDataRouteError, CacheContextError):
            _error('not_admitted', '后台来源控制暂不可用', 503)
        except (ValidationError, ValueError, TypeError):
            _error('invalid_request', '后台报价计划参数无效', 422)

    @router.post('/market/background-quote/plan')
    def read_plan(payload: dict = Body(...)):
        return plan(payload)

    @router.post('/market/background-quote')
    def read_quote(payload: dict = Body(...)):
        generation = payload.get('generation')
        request = {key: value for key, value in payload.items() if key != 'generation'}
        before = plan(request)
        if before['acquisition'] != 'public-snapshot' or generation != before['generation']:
            _error('not_admitted', '后台来源或控制版本已变化', 503, request.get('requestId'))
        from api.thesis_ledger import _real_quote
        quote = _real_quote(request['symbol'], request_id=request['requestId'],
                            asset_type=request['assetType'], background={'generation': generation})
        after = plan(request)
        if after != before:
            _error('not_admitted', '后台数据返回前来源已变化', 503, request['requestId'])
        return {**after, 'quote': quote}

    return router
