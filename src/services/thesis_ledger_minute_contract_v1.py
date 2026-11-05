"""历史分钟图表的标准查询合同；不提供日历覆盖或回测资格。"""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_security_static_contract_v1 import security_market

CAPABILITY = "MINUTE_BAR"


class MinuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    instrument_type: Literal["STOCK", "ETF"]
    start: str
    end: str

    @model_validator(mode="after")
    def identity(self):
        security_market(self.symbol)
        if (date.fromisoformat(self.start).isoformat() != self.start
                or date.fromisoformat(self.end).isoformat() != self.end or self.start > self.end):
            raise ValueError("分钟窗口必须为有序 ISO 日期")
        return self

    def route_key(self):
        return {"kind": "bar", "market": security_market(self.symbol), "assetType": self.instrument_type,
                "capability": CAPABILITY, "timeframe": "1m", "adjustment": "none"}

    @classmethod
    def from_request(cls, request):
        if (request.capability != CAPABILITY or request.timeframe != "1m" or request.adjustment != "none"
                or request.parameters != {"interactive_chart": True}):
            raise ValueError("分钟仅支持不复权正常时段交互图表")
        return cls(symbol=request.symbol, instrument_type=request.instrument_type, start=request.start, end=request.end)


class MinutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    timestamp: str
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float | None = Field(ge=0, allow_inf_nan=False)
    amount: float | None = Field(ge=0, allow_inf_nan=False)
    complete: bool

    @model_validator(mode="after")
    def values(self):
        stamp = datetime.fromisoformat(self.timestamp)
        if stamp.utcoffset() is None or stamp.second or stamp.microsecond:
            raise ValueError("分钟时间必须带时区并对齐整分钟")
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.high, self.close):
            raise ValueError("分钟 OHLC 不一致")
        return self


class MinuteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    assetType: Literal["STOCK", "ETF"]
    market: Literal["HK", "US"]
    source: str = Field(min_length=1)
    rows: list[MinutePoint] = Field(min_length=1, max_length=64_000)
    observedAt: str
    timezone: Literal["Asia/Hong_Kong", "America/New_York"]
    session: Literal["regular"]
    responseSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def observation(self):
        if datetime.fromisoformat(self.observedAt).utcoffset() is None:
            raise ValueError("分钟观察时间必须带时区")
        if security_market(self.symbol) != self.market:
            raise ValueError("分钟市场不匹配")
        if self.timezone != {"HK": "Asia/Hong_Kong", "US": "America/New_York"}[self.market]:
            raise ValueError("分钟市场时区不匹配")
        return self


def validate_binding(request, key, target):
    if request.route_key() != dict(key):
        raise ValueError("分钟请求与精确路由不匹配")


def correlate(request, result, target):
    if (result.symbol, result.assetType, result.market, result.source) != (
            request.symbol, request.instrument_type, security_market(request.symbol), target["upstreamSource"]):
        raise ValueError("分钟响应与请求或来源不匹配")
    observed, previous = datetime.fromisoformat(result.observedAt), None
    zone = ZoneInfo(result.timezone)
    for row in result.rows:
        stamp = datetime.fromisoformat(row.timestamp)
        day = stamp.astimezone(zone).date().isoformat()
        if not request.start <= day <= request.end or (previous is not None and stamp <= previous):
            raise ValueError("分钟时间重复、无序或超出请求")
        if row.complete and stamp + timedelta(minutes=1) > observed:
            raise ValueError("未结束分钟不能声明完成")
        previous = stamp
    return result


CONTRACT = CapabilityContract(CAPABILITY, MinuteRequest, MinuteResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
