"""鉴权的 V3 精确事件读取入口。"""

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from api.thesis_ledger import require_contract_token
from src.services.thesis_ledger_event_v3 import EventV3Error, execute_event_request

router = APIRouter(prefix="/thesis-ledger", tags=["ThesisLedger Events V3"])


@router.post("/market/events", dependencies=[Depends(require_contract_token)], response_model=None)
def market_events(request: Request, payload: Any = Body(...)) -> dict[str, Any] | JSONResponse:
    from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    if isinstance(payload, dict) and isinstance(payload.get("requestId"), str):
        request_id = payload["requestId"]
    try:
        return execute_event_request(
            get_thesis_ledger_runtime(), payload,
            check_security=lambda: require_contract_token(request, request.headers.get("authorization")),
        )
    except EventV3Error as error:
        status = 422 if error.code in {"invalid_request", "not_adapted", "not_admitted", "policy_not_applied"} else 503
        return JSONResponse(status_code=status, content={
            "contractVersion": 3, "requestId": request_id,
            "error": {"code": error.code, "message": "事件来源暂不可用"},
        })
