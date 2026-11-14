"""历史规则及当前静态资料的 Data Contract 入口。"""

from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, Query
from typing import Literal

from api.thesis_ledger_auth import _error, require_contract_token
from api.thesis_ledger_local_rules import build_local_rule_router
from api.thesis_ledger_static_info import build_static_info_router
from api.thesis_ledger_status_capabilities import build_status_capability_router
from api.thesis_ledger_news import build_news_router
from api.thesis_ledger_report_metadata import build_report_metadata_router
from api.thesis_ledger_reference_query import build_reference_query_router
from src.services.thesis_ledger_security_lot_contract_v1 import CAPABILITY, LotRequest
from src.services.thesis_ledger_control import ControlContractError
from src.services.thesis_ledger_current_data_route import CurrentDataRouteError
from src.services.thesis_ledger_instrument_search import search_current_instruments
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError, ThesisLedgerDataRequest, get_thesis_ledger_runtime,
)


def build_reference_data_router(get_runtime=get_thesis_ledger_runtime):
    router = APIRouter()
    from api.thesis_ledger_arena_history import build_arena_history_router
    router.include_router(build_arena_history_router(get_runtime))
    router.include_router(build_local_rule_router(get_runtime))
    router.include_router(build_static_info_router(get_runtime))
    router.include_router(build_status_capability_router(get_runtime))
    router.include_router(build_news_router(get_runtime))
    router.include_router(build_report_metadata_router(get_runtime))
    router.include_router(build_reference_query_router(get_runtime))
    from api.thesis_ledger_background_quote import build_background_quote_router
    router.include_router(build_background_quote_router(get_runtime))

    @router.get("/instruments/search", dependencies=[Depends(require_contract_token)])
    def search_instruments(
        query: str = Query(min_length=1, max_length=80),
        market: Literal["SH", "SZ", "BJ", "OF", "HK", "US"] | None = None,
        instrumentType: Literal["STOCK", "ETF", "MUTUAL_FUND"] | None = None,
    ):
        try:
            return search_current_instruments(get_runtime().store, query, market, instrumentType)
        except ControlContractError as error:
            _error(error.code.lower(), error.message, 503)
        except (ProviderCallError, CurrentDataRouteError) as error:
            status = 422 if error.code in {"invalid_request", "unsupported_capability"} else 503
            if error.code == "invalid_response":
                status = 502
            _error(error.code, str(error), status)

    @router.post("/market/security-lot-size", dependencies=[Depends(require_contract_token)])
    def read_lot_size(payload: dict = Body(...)):
        request_id = payload.get("requestId")
        if (set(payload) != {"contractVersion", "consumer", "requestId", "symbol"}
                or payload.get("contractVersion") != 3 or payload.get("consumer") != "thesis-ledger"
                or not isinstance(request_id, str) or not request_id.strip()):
            _error("bad_request", "每手股数请求格式无效", 422)
        try:
            try:
                symbol = LotRequest(symbol=payload["symbol"]).symbol
            except ValueError:
                _error("invalid_request", "每手股数需要有效港股代码", 422, request_id)
            request = ThesisLedgerDataRequest(CAPABILITY, symbol, instrument_type="STOCK", request_id=request_id)
            result = get_runtime().execute_request(request)
            return {
                "contractVersion": 3, "consumer": "thesis-ledger", "requestId": request_id,
                **result.value, "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "routeTarget": {"providerId": result.provider, "upstreamSource": result.upstream_source,
                                "routeIndex": result.route_index},
                "policyRevision": result.effective_revision,
            }
        except ProviderCallError as error:
            code = "no_eligible_provider" if error.code == "NO_ELIGIBLE_PROVIDER" else error.code
            status = 422 if code == "invalid_request" else 503
            if code == "invalid_response":
                status = 502
            _error(code, str(error), status, request_id)

    return router
