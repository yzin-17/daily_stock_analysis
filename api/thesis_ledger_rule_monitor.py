"""收费资料监测Control入口；严格输入、独立认证、持久任务查询。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.thesis_ledger_auth import require_control_token
from src.services.rule_monitor_runtime import VERSION
from src.services.rule_monitor_store import MonitorConflict

router = APIRouter(prefix="/thesis-ledger/control/rule-monitor", dependencies=[Depends(require_control_token)])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal["rule-source-monitor-v1"]
    sourceIds: list[str] = Field(max_length=9)


class Configuration(Input):
    expectedRevision: int = Field(ge=0)
    enabled: bool

    @model_validator(mode="after")
    def active_sources(self):
        if self.enabled and not self.sourceIds:
            raise ValueError("定期监测需选择来源")
        return self


class Check(Input):
    requestId: str

    @field_validator("requestId")
    @classmethod
    def identity(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("请求标识必须是规范UUID")
        return value


def runtime(request):
    result = getattr(request.app.state, "rule_monitor", None)
    if result is None:
        raise HTTPException(503, detail={"code": "rule_monitor_unavailable", "message": "资料监测尚未就绪"})
    return result


@router.get("")
def status(request: Request):
    return runtime(request).status()


@router.put("/config")
def configure(body: Configuration, request: Request):
    monitor = runtime(request)
    try:
        monitor.validate_sources(body.sourceIds, allow_empty=not body.enabled)
        config = monitor.store.configure(body.expectedRevision, body.enabled, body.sourceIds)
        monitor.wake.set()
        return {"version": VERSION, "config": config}
    except MonitorConflict as error:
        raise HTTPException(409, detail={"code": str(error), "message": "监测配置已变化，请重新读取"}) from error
    except ValueError as error:
        raise HTTPException(422, detail={"code": "bad_request", "message": "请选择已登记的监测来源"}) from error


@router.post("/checks")
def check(body: Check, request: Request):
    try:
        return {"version": VERSION, "run": runtime(request).enqueue(body.requestId, body.sourceIds)}
    except MonitorConflict as error:
        raise HTTPException(409, detail={"code": str(error), "message": "监测请求冲突或已有任务执行中"}) from error
    except ValueError as error:
        raise HTTPException(422, detail={"code": "bad_request", "message": "请选择已登记的监测来源"}) from error


@router.get("/checks/{identity}")
def job(identity: str, request: Request):
    result = runtime(request).store.job(identity)
    if result is None:
        raise HTTPException(404, detail={"code": "rule_monitor_job_not_found", "message": "未找到监测任务"})
    return {"version": VERSION, "run": result}


@router.get("/versions")
def versions(kind: Literal["pack", "document"], identity: str, request: Request):
    return {"version": VERSION, "kind": kind, "identity": identity,
        "versions": runtime(request).observations.versions(kind, identity)}
