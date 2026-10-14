"""来源日线窗口计划，只规划已证明会话，不执行数据或授予覆盖。"""

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict, model_validator


@dataclass(frozen=True)
class DailyWindowPlanningContext:
    start: str
    end: str
    sessions: tuple[str, ...]
    max_requests: int


class DailyWindow(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    start: str
    end: str

    @model_validator(mode='after')
    def ordered_dates(self):
        first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if first.isoformat() != self.start or last.isoformat() != self.end or first > last:
            raise ValueError('窗口必须是有序ISO日期')
        return self


class DailyWindowPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    windows: tuple[DailyWindow, ...]


def validate_window_plan(value: object, context: DailyWindowPlanningContext) -> DailyWindowPlan:
    if not isinstance(value, DailyWindowPlan):
        raise ValueError('来源规划器未返回标准窗口计划')
    checked = DailyWindowPlan.model_validate(value.model_dump())
    windows = checked.windows
    if (not windows or len(windows) > context.max_requests
            or windows[0].start != context.start or windows[-1].end != context.end):
        raise ValueError('来源窗口计划与请求范围或预算不匹配')
    for previous, current in zip(windows, windows[1:]):
        if (current.start <= previous.start or current.end <= previous.end
                or not any(current.start <= day <= previous.end for day in context.sessions)):
            raise ValueError('来源窗口计划必须递进且有真实会话交集')
    return checked
