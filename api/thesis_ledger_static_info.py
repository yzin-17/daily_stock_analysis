"""当前证券资料的独立 Data Contract，不写入目录或资产。"""

from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends
from api.thesis_ledger_auth import _error, require_contract_token
from src.services.thesis_ledger_security_static_contract_v1 import CAPABILITY, StaticRequest
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest


def build_static_info_router(get_runtime):
    router = APIRouter()

    @router.post("/market/security-static-info", dependencies=[Depends(require_contract_token)])
    def read_static_info(payload: dict = Body(...)):
        request_id = payload.get("requestId")
        if (set(payload) != {"contractVersion", "consumer", "requestId", "symbol", "assetType"}
                or payload.get("contractVersion") != 3 or payload.get("consumer") != "thesis-ledger"
                or not isinstance(request_id, str) or not request_id.strip()):
            _error("bad_request", "静态资料请求格式无效", 422)
        try:
            try:
                StaticRequest(symbol=payload["symbol"], instrument_type=payload["assetType"])
            except ValueError:
                _error("invalid_request", "当前资料证券或资产类型无效", 422, request_id)
            request = ThesisLedgerDataRequest(CAPABILITY, payload["symbol"], instrument_type=payload["assetType"],
                                             request_id=request_id)
            result = get_runtime().execute_request(request)
            return {
                **result.value, "contractVersion": 3, "consumer": "thesis-ledger", "requestId": request_id,
                "fetchedAt": datetime.now(timezone.utc).isoformat(), "policyRevision": result.effective_revision,
                "routeTarget": {"providerId": result.provider, "upstreamSource": result.upstream_source,
                                "routeIndex": result.route_index},
            }
        except ProviderCallError as error:
            code = "no_eligible_provider" if error.code == "NO_ELIGIBLE_PROVIDER" else error.code
            status = 503
            if code == "invalid_request":
                status = 422
            elif code == "invalid_response":
                status = 502
            _error(code, str(error), status, request_id)
    return router
