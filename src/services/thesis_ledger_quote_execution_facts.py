"""当前快照执行资料；与历史身份、历史状态及法定价格规则分别表达。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class QuoteExecutionFacts(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    version: Literal[1] = 1
    symbol: str = Field(pattern=r'^[A-Z][A-Z0-9.-]*\.US$')
    market: Literal['US'] = 'US'
    assetType: Literal['STOCK', 'ETF']
    exchange: str | None
    securityId: str | None
    sharesPerLot: str | None = Field(default=None, pattern=r'^[1-9][0-9]*$')
    delisted: bool | None
    tradingState: Literal['normal', 'suspended', 'unknown']
    securityStatus: str | None
    marketTime: str | None
    observedAt: str
    provider: Literal['futu'] = 'futu'
    responseSha256: str = Field(pattern=r'^[a-f0-9]{64}$')
