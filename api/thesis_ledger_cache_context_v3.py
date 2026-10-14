"""原Data V3认证的只读缓存代次上下文。"""

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from api.thesis_ledger_auth import require_contract_token
from src.services.thesis_ledger_cache_context_v3 import read_cache_context, CacheContextError
from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

router = APIRouter(prefix='/thesis-ledger', tags=['ThesisLedger Market Contract V3'])


@router.post('/market/cache-context', dependencies=[Depends(require_contract_token)], response_model=None)
def cache_context(payload: Any = Body(...)):
    request_id = payload.get('requestId') if isinstance(payload, dict) else None
    if not isinstance(request_id, str) or not request_id.strip():
        request_id = str(uuid4())
    try:
        return read_cache_context(get_thesis_ledger_runtime(), payload)
    except ValidationError:
        code, status, message = 'invalid_request', 422, '来源拒绝请求参数'
    except CacheContextError as error:
        if error.code == 'invalid_response':
            code, status, message = 'invalid_response', 502, '行情上游响应格式无效'
        else:
            code, status, message = 'upstream_failure', 503, '行情上游暂时不可用'
    return JSONResponse(status_code=status, content={
        'contractVersion': 3, 'requestId': request_id,
        'error': {'code': code, 'message': message},
    })
