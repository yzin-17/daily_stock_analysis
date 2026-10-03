"""ThesisLedger 页面授权接口，沿用独立 Control Token。"""

import os

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from api.thesis_ledger import (
    _control_v3_envelope,
    _control_http_error,
    _control_store,
    require_control_token,
)
from src.services.provider_oauth_manager import ProviderOAuthManager
from src.services.provider_oauth_store import OAuthStateError, ProviderOAuthStore
from src.services.thesis_ledger_control import ControlContractError, _encrypt_secret


def prevent_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/thesis-ledger/control/providers/longbridge/oauth",
    dependencies=[Depends(require_control_token), Depends(prevent_cache)],
)


def initialize_provider_oauth(app) -> None:
    if not os.getenv("THESIS_LEDGER_CONTROL_TOKEN", "").strip():
        return
    store = _control_store()
    repository = ProviderOAuthStore(store.database_path, encrypt=_encrypt_secret)
    manager = ProviderOAuthManager(
        repository, callback_host=os.getenv("THESIS_LEDGER_OAUTH_CALLBACK_HOST", "127.0.0.1")
    )
    manager.recover()
    app.state.provider_oauth_manager = manager


async def shutdown_provider_oauth(app) -> None:
    manager = getattr(app.state, "provider_oauth_manager", None)
    if manager:
        await manager.shutdown()
        delattr(app.state, "provider_oauth_manager")


def _manager(request: Request) -> ProviderOAuthManager:
    manager = getattr(request.app.state, "provider_oauth_manager", None)
    if manager is None:
        raise OAuthStateError("OAUTH_SERVICE_UNAVAILABLE")
    return manager


def _error(error: Exception):
    if isinstance(error, HTTPException):
        raise error
    if isinstance(error, ControlContractError):
        _control_http_error(error)
    code = error.code if isinstance(error, OAuthStateError) else "OAUTH_STORAGE_UNAVAILABLE"
    status = 400
    if code == "OAUTH_SESSION_NOT_FOUND":
        status = 404
    elif code in {"OAUTH_ALREADY_PENDING", "OAUTH_PROVIDER_REMOVED", "OAUTH_CONFIG_CHANGED"}:
        status = 409
    elif code in {
        "OAUTH_SDK_UNAVAILABLE",
        "OAUTH_SERVICE_UNAVAILABLE",
        "OAUTH_STORAGE_UNAVAILABLE",
    }:
        status = 503
    raise HTTPException(
        status_code=status,
        detail={
            "contractVersion": 3,
            "code": code,
            "message": "Longbridge 浏览器授权未完成，请检查授权状态。",
        },
    ) from None


@router.post("/sessions")
async def create_session(request: Request, payload: dict = Body(...)):
    try:
        value = _control_v3_envelope(payload)
        if set(value) - {"clientId", "contractVersion", "consumer", "requestId"}:
            raise OAuthStateError("OAUTH_REQUEST_INVALID")
        session = await _manager(request).create(value.get("clientId"))
        return {"contractVersion": 3, "consumer": "thesis-ledger", **session}
    except Exception as error:
        _error(error)


@router.get("/sessions/current")
async def current_session(request: Request):
    try:
        return {"contractVersion": 3, "consumer": "thesis-ledger", "session": _manager(request).current()}
    except Exception as error:
        _error(error)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    try:
        return {"contractVersion": 3, "consumer": "thesis-ledger", **_manager(request).get(session_id)}
    except Exception as error:
        _error(error)


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: Request, payload: dict = Body(...)):
    try:
        value = _control_v3_envelope(payload)
        if set(value) != {"contractVersion", "consumer", "requestId"}:
            raise OAuthStateError("OAUTH_REQUEST_INVALID")
        session = await _manager(request).cancel(session_id)
        return {"contractVersion": 3, "consumer": "thesis-ledger", **session}
    except Exception as error:
        _error(error)
