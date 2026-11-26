"""历史分钟图表的标准查询合同；不提供日历覆盖或回测资格。"""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_security_static_contract_v1 import security_market

CAPABILITY = "MINUTE_BAR"
TIMEFRAMES = ("1m", "5m", "15m", "30m", "60m")
IntradayTimeframe = Literal["1m", "5m", "15m", "30m", "60m"]
MARKET_TIMEZONES = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}


def minute_market(symbol):
    if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol):
        return 'CN'
    return security_market(symbol)


class MinuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    instrument_type: Literal["STOCK", "ETF"]
    start: str
    end: str
    timeframe: IntradayTimeframe = "1m"

    @model_validator(mode="after")
    def identity(self):
        minute_market(self.symbol)
        if (date.fromisoformat(self.start).isoformat() != self.start
                or date.fromisoformat(self.end).isoformat() != self.end or self.start > self.end):
            raise ValueError("分钟窗口必须为有序 ISO 日期")
        return self

    def route_key(self):
        return {"kind": "bar", "market": minute_market(self.symbol), "assetType": self.instrument_type,
                "capability": CAPABILITY, "timeframe": self.timeframe, "adjustment": "none"}

    @classmethod
    def from_request(cls, request):
        if (request.capability != CAPABILITY or request.timeframe not in TIMEFRAMES or request.adjustment != "none"
                or request.parameters not in ({"interactive_chart": True}, {"historical_reconstruction": True})):
            raise ValueError("分钟仅支持显式不复权原生资料读取")
        return cls(symbol=request.symbol, instrument_type=request.instrument_type, start=request.start,
                   end=request.end, timeframe=request.timeframe)


class MinutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    timestamp: str
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float | None = Field(ge=0, allow_inf_nan=False)
    amount: float | None = Field(ge=0, allow_inf_nan=False)
    complete: bool | None

    @model_validator(mode="after")
    def values(self):
        stamp = datetime.fromisoformat(self.timestamp)
        if stamp.utcoffset() is None or stamp.second or stamp.microsecond:
            raise ValueError("分钟时间必须带时区并对齐整分钟")
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.high, self.close):
            raise ValueError("分钟 OHLC 不一致")
        return self


class MinutePagination(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    requestedStart: str
    requestedEnd: str
    pagesFetched: int = Field(ge=1, le=64)
    continuationPending: bool = Field(strict=True)


class MinuteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    assetType: Literal["STOCK", "ETF"]
    market: Literal["CN", "HK", "US"]
    source: str = Field(min_length=1)
    rows: list[MinutePoint] = Field(min_length=1, max_length=64_000)
    observedAt: str
    timezone: Literal["Asia/Shanghai", "Asia/Hong_Kong", "America/New_York"]
    timeframe: IntradayTimeframe = "1m"
    timestampConvention: Literal["interval-start", "interval-end", "source-native"] = "interval-start"
    fieldUnits: dict[str, str] = Field(default_factory=lambda: {"volume": "unknown", "amount": "unknown"})
    session: Literal["regular"]
    responseSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pagination: MinutePagination | None = None

    @model_validator(mode="after")
    def observation(self):
        if datetime.fromisoformat(self.observedAt).utcoffset() is None:
            raise ValueError("分钟观察时间必须带时区")
        if minute_market(self.symbol) != self.market:
            raise ValueError("分钟市场不匹配")
        if self.timezone != MARKET_TIMEZONES[self.market]:
            raise ValueError("分钟市场时区不匹配")
        if self.timestampConvention == 'source-native' and any(row.complete is not None for row in self.rows):
            raise ValueError("来源未说明时间约定时不能声明分钟完成状态")
        if (set(self.fieldUnits) != {"volume", "amount"}
                or self.fieldUnits["volume"] not in {"hand", "share", "fund-unit", "unknown"}
                or self.fieldUnits["amount"] not in {"CNY", "unknown"}):
            raise ValueError("分钟量额单位无效")
        return self


def validate_binding(request, key, target):
    if request.route_key() != dict(key):
        raise ValueError("分钟请求与精确路由不匹配")


def correlate(request, result, target):
    pages = result.pagination
    if pages and (pages.continuationPending or (pages.requestedStart, pages.requestedEnd) != (request.start, request.end)):
        raise ValueError("分钟分页未结束或分页日期与请求不一致")
    if (result.symbol, result.assetType, result.market, result.source, result.timeframe) != (
            request.symbol, request.instrument_type, minute_market(request.symbol), target["upstreamSource"], request.timeframe):
        raise ValueError("分钟响应与请求或来源不匹配")
    observed, previous = datetime.fromisoformat(result.observedAt), None
    zone = ZoneInfo(result.timezone)
    for row in result.rows:
        stamp = datetime.fromisoformat(row.timestamp)
        if stamp.utcoffset() != stamp.astimezone(zone).utcoffset():
            raise ValueError("分钟时间偏移与市场时区不匹配")
        day = stamp.astimezone(zone).date().isoformat()
        if not request.start <= day <= request.end or (previous is not None and stamp <= previous):
            raise ValueError("分钟时间重复、无序或超出请求")
        finish = stamp
        if result.timestampConvention == "interval-start":
            finish += timedelta(minutes=int(request.timeframe[:-1]))
        if stamp > observed or row.complete and finish > observed:
            raise ValueError("未结束分钟不能声明完成")
        previous = stamp
    return result


CONTRACT = CapabilityContract(CAPABILITY, MinuteRequest, MinuteResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
