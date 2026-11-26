"""本地历史规则通过既有认证和精确路由读取；不执行隐式选源。"""

from collections.abc import Mapping
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Body, Depends

from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_market_errors_v3 import market_runtime_error
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError, ThesisLedgerDataRequest, get_thesis_ledger_runtime,
)
from src.services.thesis_ledger_public_fees import CAPABILITY as FEES_CAPABILITY


def build_rule_data_router(capability: str, path: str, label: str,
                           get_runtime: Callable[..., Any] = get_thesis_ledger_runtime) -> APIRouter:
    router = APIRouter()

    @router.post(path, dependencies=[Depends(require_contract_token)])
    def read_rules(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            market_data_error_v3("invalid_request", 422, str(uuid4()))
        if (type(payload.get("contractVersion")) is not int or payload.get("contractVersion") != 1):
            market_data_error_v3("unsupported_data_contract_version", 422, request_id)
        if payload.get("consumer") != "thesis-ledger":
            market_data_error_v3("invalid_request", 422, request_id)
        key, target, window = (payload.get(field) for field in ("routeKey", "routeTarget", "range"))
        if not all(isinstance(value, Mapping) for value in (key, target, window)):
            market_data_error_v3("invalid_request", 422, request_id)
        assets = ("STOCK", "ETF") if capability == FEES_CAPABILITY else ("STOCK",)
        if key.get("kind") != "data" or key.get("capability") != capability or key.get("assetType") not in assets:
            market_data_error_v3("unsupported_capability", 422, request_id)
        try:
            request = ThesisLedgerDataRequest(
                capability, payload.get("symbol"), instrument_type=key["assetType"],
                start=window.get("start"), end=window.get("end"),
                request_id=request_id, parameters=payload,
            )
            return get_runtime().execute_request(request).value
        except ProviderCallError as exc:
            if exc.code == "unsupported_capability":
                market_data_error_v3("unsupported_capability", 422, request_id)
            code, status = market_runtime_error(exc)
            market_data_error_v3(code, status, request_id)

    return router


def build_local_rule_router(get_runtime: Callable[..., Any] = get_thesis_ledger_runtime) -> APIRouter:
    from api.thesis_ledger_announcements import build_announcement_router
    router = APIRouter()
    router.include_router(build_announcement_router())
    router.include_router(build_rule_data_router(FEES_CAPABILITY, "/market/public-fees", "公共收费", get_runtime))
    return router
