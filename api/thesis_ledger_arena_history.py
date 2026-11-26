"""历史回放专用资料入口；沿用 Data Contract 认证。"""

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError
from api.thesis_ledger_auth import _error, require_contract_token
from src.services.thesis_ledger_arena_history import (
    HistoryRequest, MinuteHistoryRequest, read_catalog, read_minutes,
)
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerGatewayError
from src.services.thesis_ledger_arena_instruments import InstrumentDiscoveryRequest, read_instruments
from src.services.thesis_ledger_historical_execution import ExecutionRequest
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_market_errors_v3 import market_runtime_error


def build_arena_history_router(get_runtime):
    router = APIRouter()

    def read(payload, model, operation):
        try:
            request = model.model_validate(payload)
            return operation(get_runtime(), request)
        except (ValidationError, ValueError):
            _error("invalid_request", "历史资料请求无效", 422, payload.get("requestId"))
        except (ProviderCallError, ThesisLedgerGatewayError) as error:
            _error(error.code.lower(), str(error), 503, payload.get("requestId"))

    @router.post("/arena/history/catalog", dependencies=[Depends(require_contract_token)])
    def catalog(payload: dict = Body(...)):
        return read(payload, HistoryRequest, read_catalog)

    @router.post("/arena/history/minutes", dependencies=[Depends(require_contract_token)])
    def minutes(payload: dict = Body(...)):
        return read(payload, MinuteHistoryRequest, read_minutes)

    @router.post("/arena/history/instruments", dependencies=[Depends(require_contract_token)])
    def instruments(payload: dict = Body(...)):
        return read(payload, InstrumentDiscoveryRequest, read_instruments)

    @router.post('/arena/history/execution', dependencies=[Depends(require_contract_token)])
    def execution(payload: dict = Body(...)):
        request_id = payload.get('requestId')
        if not isinstance(request_id, str) or not request_id.strip():
            from uuid import uuid4
            request_id = str(uuid4())
        try:
            query = ExecutionRequest.model_validate(payload)
            return get_runtime().execute_request(ThesisLedgerDataRequest(
                query.routeKey.capability, query.symbol, instrument_type=query.routeKey.assetType,
                start=query.range.start, end=query.range.end,
                request_id=query.requestId, parameters=query.model_dump())).value
        except (ValidationError, ValueError):
            market_data_error_v3('invalid_request', 422, request_id)
        except (ProviderCallError, ThesisLedgerGatewayError) as error:
            code, status = market_runtime_error(error)
            market_data_error_v3(code, status, request_id)

    return router
