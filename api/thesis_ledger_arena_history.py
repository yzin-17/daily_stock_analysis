"""历史回放专用资料入口；沿用 Data Contract 认证。"""

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError
from api.thesis_ledger_auth import _error, require_contract_token
from src.services.thesis_ledger_arena_history import (
    HistoryRequest, MinuteHistoryRequest, read_catalog, read_minutes,
)
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerGatewayError
from src.services.thesis_ledger_arena_instruments import InstrumentDiscoveryRequest, read_instruments


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

    return router
