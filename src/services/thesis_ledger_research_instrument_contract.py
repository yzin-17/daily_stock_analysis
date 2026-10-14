"""原InstrumentFact的研究观察子集；字符串时钟及单位未知保持。"""

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from src.services.thesis_ledger_event_time_v1 import instant


class ResearchInstrumentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    symbol: str = Field(min_length=1)
    market: Literal['HK', 'US']
    start: str
    end: str
    data_as_of: datetime

    @model_validator(mode='after')
    def window_and_clock(self):
        first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if first.isoformat() != self.start or last.isoformat() != self.end or first > last:
            raise ValueError('研究身份事实窗口无效')
        if self.data_as_of.utcoffset() is None or last > self.data_as_of.date():
            raise ValueError('研究身份事实截点无效')
        return self


class UnavailableResearchRules(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    status: Literal['unavailable']
    reason: str = Field(min_length=1)


class ResearchInstrumentFact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    symbol: str = Field(min_length=1)
    market: Literal['HK', 'US']
    instrumentType: Literal['STOCK']
    currency: Literal['HKD', 'USD']
    lotSize: str | None
    tickSize: str | None
    tradable: bool
    executionRules: UnavailableResearchRules
    provider: str = Field(min_length=1)
    providerRevision: str = Field(min_length=1)
    occurredAt: str
    availableAt: str

    @field_validator('lotSize', 'tickSize')
    @classmethod
    def positive_units(cls, value):
        if value is not None and (not re.fullmatch(r'(?:0|[1-9]\d*)(?:\.\d+)?', value) or not re.search(r'[1-9]', value)):
            raise ValueError('研究单位必须是正规范十进制字符串或未知')
        return value

    @field_validator('occurredAt', 'availableAt')
    @classmethod
    def source_clock(cls, value):
        instant(value)
        return value

    @model_validator(mode='after')
    def observation_only(self):
        if self.tradable or (self.lotSize is None) != (self.tickSize is None):
            raise ValueError('研究观察不能声明可交易或单边已知单位')
        if self.currency != {'HK': 'HKD', 'US': 'USD'}[self.market]:
            raise ValueError('研究身份币种与市场不符')
        return self


@dataclass(frozen=True)
class ResearchInstrumentPolicy:
    supports_symbol: Callable[[str], bool]
    read: Callable[[ResearchInstrumentRequest], ResearchInstrumentFact]


def correlate_research_instrument(request: ResearchInstrumentRequest, value: object, upstream_source: str) -> ResearchInstrumentFact:
    if not isinstance(value, ResearchInstrumentFact):
        raise ValueError('来源未返回标准研究身份事实')
    result = ResearchInstrumentFact.model_validate(value.model_dump())
    cutoff = instant(request.data_as_of.isoformat())
    if (result.symbol != request.symbol or result.market != request.market or result.provider != upstream_source
            or instant(result.occurredAt) > cutoff or instant(result.availableAt) > cutoff):
        raise ValueError('研究身份事实与请求、来源或截点不符')
    return result
