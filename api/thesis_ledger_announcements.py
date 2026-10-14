"""公告目录查询与显式获取；使用现有契约认证。"""
from datetime import date
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from api.thesis_ledger_auth import _error, require_contract_token, require_control_token
from src.services.thesis_ledger_announcements import AnnouncementRepository


class AnnouncementFetch(BaseModel):
    symbols: list[Annotated[str, Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")]] = Field(min_length=1, max_length=5)
    start: date
    end: date
    maxPages: int = Field(default=5, ge=1, le=5)
    parse: bool = True


class AnnouncementPreparation(BaseModel):
    symbols: list[Annotated[str, Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")]] = Field(min_length=1, max_length=5)
    start: date
    end: date


def build_announcement_router():
    router = APIRouter()

    @router.get("/market/announcements", dependencies=[Depends(require_contract_token)])
    def announcements(q: str = Query("", max_length=200), page: int = Query(1, ge=1), pageSize: int = Query(50, ge=1, le=100)):
        try:
            return AnnouncementRepository().query(q, page, pageSize)
        except (ValueError, OSError) as exc:
            _error("invalid_response", f"公告库无法读取：{exc}", 503)

    @router.post("/control/announcements/fetch", dependencies=[Depends(require_control_token)])
    def fetch_announcements(payload: AnnouncementFetch):
        if not 0 <= (payload.end - payload.start).days <= 31:
            _error("bad_request", "一次公告获取区间应为0至31天", 422)
        from data_provider.cninfo_announcements import acquire_announcements
        return acquire_announcements(payload.symbols, payload.start.isoformat(), payload.end.isoformat(),
                                     max_pages=payload.maxPages, parse=payload.parse)

    @router.post("/control/announcements/prepare", dependencies=[Depends(require_control_token)])
    def prepare_announcements(payload: AnnouncementPreparation, request: Request):
        runtime = getattr(request.app.state, "announcement_runtime", None)
        if runtime is None:
            _error("unavailable", "公告后台获取服务尚未启动", 503)
        try:
            return runtime.prepare(payload.symbols, payload.start.isoformat(), payload.end.isoformat())
        except ValueError as exc:
            _error("bad_request", str(exc), 422)
        except OSError as exc:
            _error("unavailable", f"公告任务无法写入：{exc}", 503)

    return router
