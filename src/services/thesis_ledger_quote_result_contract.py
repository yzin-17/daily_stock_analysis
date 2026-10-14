"""报价标准结果：复制基本字段，不携带SDK对象或私有单位字典。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.services.thesis_ledger_quote_units_contract import QuotePublicUnits


class QuoteUnitFailure(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    code: Literal['upstream_invalid_response'] = 'upstream_invalid_response'
    status_code: Literal[502] = 502
    message: str = Field(min_length=1)


class UnverifiedQuoteTime(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    basis: Literal['unverified'] = 'unverified'
    text: str


class QuoteResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)
    price: float = Field(gt=0)
    open_price: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    pre_close: float = Field(ge=0)
    volume: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0)
    change_amount: float | None = None
    change_pct: float | None = None
    code: str | None = None
    name: str | None = None
    source: str | None = None
    upstream_source: str | None = None
    provider_timestamp: str | None = None
    source_time_observation: UnverifiedQuoteTime | None = None
    fetched_at: str | datetime | None = None
    is_stale: bool | None = None
    units: QuotePublicUnits | None = None
    unit_error: QuoteUnitFailure | None = None

    @model_validator(mode='after')
    def consistent_result(self):
        if self.high < max(self.open_price, self.low, self.price) or self.low > min(self.open_price, self.high, self.price):
            raise ValueError('报价OHLC不符')
        if self.units is not None and self.unit_error is not None:
            raise ValueError('报价单位与单位错误不能并存')
        return self
