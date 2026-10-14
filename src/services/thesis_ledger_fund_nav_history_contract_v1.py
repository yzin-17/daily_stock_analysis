"""普通基金净值历史的标准序列合同；不改变精确 NAV 研究协议。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_fund_nav_contract_v1 import NavPoint, fund_symbol

CAPABILITY = 'FUND_NAV_HISTORY'


class NavHistoryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    symbol: str
    instrument_type: Literal['MUTUAL_FUND'] = 'MUTUAL_FUND'
    start: str | None = None
    end: str | None = None
    limit: int | None = Field(default=None, gt=0)
    _symbol = field_validator('symbol')(fund_symbol)

    @model_validator(mode='after')
    def window(self):
        for value in (self.start, self.end):
            if value is not None and date.fromisoformat(value).isoformat() != value:
                raise ValueError('历史窗口须为 ISO 估值日')
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError('历史窗口逆序')
        return self

    def route_key(self):
        return {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': CAPABILITY}

    @classmethod
    def from_request(cls, request):
        if request.parameters or request.timeframe is not None or request.adjustment is not None:
            raise ValueError('净值历史不接受周期、复权或供应商参数')
        return cls(symbol=request.symbol, instrument_type=request.instrument_type or 'MUTUAL_FUND',
                   start=request.start, end=request.end, limit=request.limit)


class NavHistoryResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')
    symbol: str
    capability: Literal['FUND_NAV_HISTORY'] = CAPABILITY
    points: tuple[NavPoint, ...] = Field(min_length=1)
    provider: str = Field(min_length=1)
    upstream_source: str = Field(min_length=1)
    observed_at: str
    date_basis: Literal['valuation_date'] = 'valuation_date'
    publication_visibility: Literal['unverified'] = 'unverified'
    identity_basis: Literal['request_bound'] = 'request_bound'
    source_reported_end: str | None = None
    _symbol = field_validator('symbol')(fund_symbol)

    @model_validator(mode='after')
    def sequence(self):
        if self.source_reported_end is not None and (
                date.fromisoformat(self.source_reported_end).isoformat() != self.source_reported_end
                or self.source_reported_end < max(point.reported_date for point in self.points)):
            raise ValueError('完整来源的报告日上界非法')
        dates = [datetime.fromisoformat(point.nav_date) for point in self.points]
        if any(first >= second for first, second in zip(dates, dates[1:])):
            raise ValueError('净值历史须严格升序且日期唯一')
        if datetime.fromisoformat(self.observed_at).utcoffset() is None:
            raise ValueError('处理观察时间须有时区')
        return self


def validate_binding(request, key, target):
    if dict(key) != request.route_key():
        raise ValueError('净值历史请求与能力身份不匹配')


def correlate(request, result, target):
    if (result.symbol, result.provider, result.upstream_source) != (
            request.symbol, target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('净值历史结果与证券或来源不匹配')
    if request.limit is not None and len(result.points) > request.limit:
        raise ValueError('净值历史超过请求限量')
    if any(request.start is not None and point.reported_date < request.start
           or request.end is not None and point.reported_date > request.end for point in result.points):
        raise ValueError('净值历史超出请求估值日窗口')
    return result


CONTRACT = CapabilityContract(CAPABILITY, NavHistoryRequest, NavHistoryResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
