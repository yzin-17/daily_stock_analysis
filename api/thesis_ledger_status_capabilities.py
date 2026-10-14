"""五种强类型独立状态能力的 Data V3 入口；仍交给既有执行器。"""

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError
from uuid import uuid4
from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_status_capabilities_v1 import REQUESTS
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest
from src.services.thesis_ledger_market_errors_v3 import market_runtime_error


def build_status_capability_router(get_runtime):
    router = APIRouter()

    @router.post("/market/status-capabilities", dependencies=[Depends(require_contract_token)])
    def read(payload: dict = Body(...)):
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = str(uuid4())
        try:
            query = REQUESTS.validate_python(payload)
        except ValidationError:
            market_data_error_v3("invalid_request", 422, request_id)
        window = getattr(query, "range", None)
        request = ThesisLedgerDataRequest(query.routeKey.capability, query.symbol, instrument_type=query.routeKey.assetType,
                                         start=window.start if window else None, end=window.end if window else None,
                                         request_id=query.requestId, parameters=query.model_dump())
        try:
            return get_runtime().execute_request(request).value
        except ProviderCallError as error:
            if error.code == "NO_ELIGIBLE_PROVIDER":
                market_data_error_v3("upstream_failure", 503, query.requestId)
            if error.code == "unsupported_capability":
                market_data_error_v3("unsupported_capability", 422, query.requestId)
            code, status = market_runtime_error(error)
            market_data_error_v3(code, status, query.requestId)
    return router
