"""本地历史规则通过既有认证和精确路由读取；不执行隐式选源。"""

from collections.abc import Mapping
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends

from api.thesis_ledger_auth import _error, require_contract_token
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError, ThesisLedgerDataRequest, get_thesis_ledger_runtime,
)
from src.services.thesis_ledger_public_fees import CAPABILITY as FEES_CAPABILITY
from src.services.thesis_ledger_trading_status import CAPABILITY as STATUS_CAPABILITY


def build_rule_data_router(capability: str, path: str, label: str,
                           get_runtime: Callable[..., Any] = get_thesis_ledger_runtime) -> APIRouter:
    router = APIRouter()

    @router.post(path, dependencies=[Depends(require_contract_token)])
    def read_rules(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            _error("bad_request", f"{label}请求缺少 requestId", 422)
        if payload.get("contractVersion") != 3:
            _error("unsupported_data_contract_version", f"{label}只支持 Data Contract V3", 422, request_id)
        if payload.get("consumer") != "thesis-ledger":
            _error("bad_request", f"{label}请求 consumer 不匹配", 422, request_id)
        key, target, window = (payload.get(field) for field in ("routeKey", "routeTarget", "range"))
        if not all(isinstance(value, Mapping) for value in (key, target, window)):
            _error("bad_request", f"{label}请求缺少路由或日期区间", 422, request_id)
        if key.get("kind") != "data" or key.get("capability") != capability or key.get("assetType") != "STOCK":
            _error("unsupported_capability", f"{label}请求能力不匹配", 422, request_id)
        try:
            request = ThesisLedgerDataRequest(
                capability, payload.get("symbol"), instrument_type=key["assetType"],
                start=window.get("start"), end=window.get("end"),
                request_id=request_id, parameters=payload,
            )
            return get_runtime().execute_request(request).value
        except ProviderCallError as exc:
            code, status = exc.code, 503
            if code == "NO_ELIGIBLE_PROVIDER":
                code = "no_eligible_provider"
            elif code == "invalid_request":
                code, status = "bad_request", 422
            elif code == "unsupported_capability":
                status = 422
            elif code == "invalid_response":
                status = 502
            _error(code, str(exc), status, request_id)

    return router


def build_local_rule_router(get_runtime: Callable[..., Any] = get_thesis_ledger_runtime) -> APIRouter:
    from api.thesis_ledger_announcements import build_announcement_router
    router = APIRouter()
    router.include_router(build_announcement_router())
    router.include_router(build_rule_data_router(FEES_CAPABILITY, "/market/public-fees", "公共收费", get_runtime))
    router.include_router(build_rule_data_router(STATUS_CAPABILITY, "/market/trading-status", "交易状态", get_runtime))
    return router
