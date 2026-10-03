"""鉴权的 V3 精确 NAV 冻结输入生产入口。"""

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from api.thesis_ledger import require_contract_token
from src.services.thesis_ledger_nav_production import execute_nav_request
from src.services.thesis_ledger_nav_request import NavProductionError


router = APIRouter(prefix='/thesis-ledger', tags=['ThesisLedger NAV V3'])


@router.post('/backtest/nav-inputs', dependencies=[Depends(require_contract_token)], response_model=None)
def nav_inputs(request: Request, payload: Any = Body(...)) -> dict[str, Any] | JSONResponse:
    from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

    request_id = request.headers.get('x-request-id') or str(uuid.uuid4())
    if isinstance(payload, dict) and isinstance(payload.get('requestId'), str):
        request_id = payload['requestId']
    try:
        return execute_nav_request(get_thesis_ledger_runtime(), payload, check_security=lambda: (
            require_contract_token(request, request.headers.get('authorization'))))
    except NavProductionError as error:
        status = 503 if error.code == 'upstream_failure' else 422
        return JSONResponse(status_code=status, content={'contractVersion': 3, 'requestId': request_id,
                            'error': {'code': error.code, 'message': '净值来源证据暂不可用'}})
