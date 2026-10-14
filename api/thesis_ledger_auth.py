"""ThesisLedger Data/Control Token 与共享请求错误上下文。"""

from __future__ import annotations

import os
import uuid
from contextvars import ContextVar
from typing import Optional

from fastapi import Header, HTTPException, Request

CONTRACT_VERSION = 3
_request_id_context: ContextVar[str | None] = ContextVar("thesis_ledger_request_id", default=None)


def _error(code: str, message: str, status_code: int, request_id: str | None = None) -> None:
    stable_request_id = request_id or _request_id_context.get() or str(uuid.uuid4())
    raise HTTPException(
        status_code=status_code,
        detail={
            "contractVersion": CONTRACT_VERSION,
            "code": code,
            "message": message,
            "requestId": stable_request_id,
            "diagnosticId": stable_request_id,
        },
    )


def require_contract_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> None:
    """校验 ThesisLedger 专用 token，不依赖 DSA 管理员 session。"""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    _request_id_context.set(request_id)
    expected = os.getenv("THESIS_LEDGER_DSA_TOKEN", "").strip()
    if not expected:
        _error("service_misconfigured", "THESIS_LEDGER_DSA_TOKEN 未配置", 503, request_id)
    if authorization != f"Bearer {expected}":
        _error("unauthorized", "需要有效的 ThesisLedger DSA Bearer Token", 401, request_id)


def require_control_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> None:
    """校验独立 Control Token；客户端永远不应获得该 token。"""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    _request_id_context.set(request_id)
    expected = os.getenv("THESIS_LEDGER_CONTROL_TOKEN", "").strip()
    if not expected:
        _error("service_misconfigured", "THESIS_LEDGER_CONTROL_TOKEN 未配置", 503, request_id)
    if authorization != f"Bearer {expected}":
        _error("unauthorized", "需要有效的 ThesisLedger Control Token", 401, request_id)
