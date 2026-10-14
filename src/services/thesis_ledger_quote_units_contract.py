"""公开报价单位投影；沿原市场Schema和HTTP错误生命周期。"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict
from src.services.thesis_ledger_capability_contracts import QuoteValue


class QuotePublicUnits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    priceCurrency: Literal['CNY', 'HKD', 'USD']
    volume: Literal['share', 'unknown']
    turnoverCurrency: Literal['CNY', 'unknown']


@dataclass(frozen=True)
class QuotePublicUnitsContext:
    value: QuoteValue
    upstream_source: str | None


class QuotePublicUnitsError(ValueError):
    code = 'upstream_invalid_response'
    status_code = 502

    def __init__(self, message='报价单位合同无效'):
        super().__init__(message)
