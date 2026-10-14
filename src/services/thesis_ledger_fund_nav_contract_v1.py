"""最新基金净值合同；估值日期不表示公告可见时间。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_fund_identity_v1 import fund_symbol

CAPABILITY = 'FUND_NAV'




class NavPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False, revalidate_instances='always')
    reported_date: str
    nav_date: str
    unit_nav: float = Field(gt=0)

    @model_validator(mode='after')
    def dates(self):
        if date.fromisoformat(self.reported_date).isoformat() != self.reported_date:
            raise ValueError('报告估值日须为 ISO 日期')
        parsed = datetime.fromisoformat(self.nav_date)
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError('兼容净值日期编码须为 UTC')
        return self


class LatestNavRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    symbol: str
    instrument_type: Literal['MUTUAL_FUND'] = 'MUTUAL_FUND'
    _symbol = field_validator('symbol')(fund_symbol)

    def route_key(self):
        return {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': CAPABILITY}

    @classmethod
    def from_request(cls, request):
        if request.parameters or any(getattr(request, name) is not None for name in ('start', 'end', 'limit', 'timeframe', 'adjustment')):
            raise ValueError('最新净值不接受历史、限量、周期、复权或额外参数')
        return cls(symbol=request.symbol, instrument_type=request.instrument_type or 'MUTUAL_FUND')


class LatestNavResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')
    symbol: str
    capability: Literal['FUND_NAV'] = CAPABILITY
    point: NavPoint
    provider: str = Field(min_length=1)
    upstream_source: str = Field(min_length=1)
    observed_at: str
    date_basis: Literal['valuation_date'] = 'valuation_date'
    publication_visibility: Literal['unverified'] = 'unverified'
    identity_basis: Literal['request_bound'] = 'request_bound'
    source_reported_end: str | None = None
    _symbol = field_validator('symbol')(fund_symbol)

    @model_validator(mode='after')
    def clock(self):
        if self.source_reported_end is not None and (
                date.fromisoformat(self.source_reported_end).isoformat() != self.source_reported_end
                or self.source_reported_end < self.point.reported_date):
            raise ValueError('完整来源的报告日上界非法')
        if datetime.fromisoformat(self.observed_at).utcoffset() is None:
            raise ValueError('处理观察时间须有时区')
        return self


def validate_binding(request, key, target):
    if dict(key) != request.route_key():
        raise ValueError('最新净值请求与能力身份不匹配')


def correlate(request, result, target):
    if (result.symbol, result.provider, result.upstream_source) != (
            request.symbol, target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('最新净值结果与证券或来源不匹配')
    return result


CONTRACT = CapabilityContract(CAPABILITY, LatestNavRequest, LatestNavResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
