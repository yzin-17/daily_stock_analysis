"""最小盘口能力合同；仅供隔离注册及生命周期验证。"""

from decimal import Decimal
from typing import Literal, Mapping
from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BookModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("symbol", "venue", "providerId", "upstreamSource", "adapterRevision", check_fields=False)
    @classmethod
    def identifiers(cls, value):
        if value is not None and (not value or value.strip() != value):
            raise ValueError("标识不得为空或包含首尾空白")
        return value

    @field_validator("symbol", check_fields=False)
    @classmethod
    def canonical_symbol(cls, value):
        if not re.fullmatch(r"(?:[0-9]{6}\.(?:SH|SZ|BJ)|[0-9]{5}\.HK|\^?[A-Z][A-Z0-9.-]{0,31}\.US)", value) or value == "00000.HK":
            raise ValueError("证券代码必须使用规范市场格式")
        return value

    @model_validator(mode="after")
    def symbol_market(self):
        if hasattr(self, "symbol"):
            suffix = self.symbol.rsplit(".", 1)[1]
            market = suffix if suffix in {"HK", "US"} else "CN"
            if self.market != market:
                raise ValueError("证券代码与市场不一致")
        return self


class OrderBookRequest(BookModel):
    symbol: str = Field(min_length=1)
    market: Literal["CN", "HK", "US"]
    depth: int = Field(ge=1, le=100)
    allowDepthDegradation: bool = False


class Level(BookModel):
    price: str = Field(max_length=64, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    quantity: str = Field(max_length=64, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    orderCount: int | None = Field(ge=0, le=9007199254740991)

    @model_validator(mode="after")
    def numbers(self):
        if Decimal(self.price) <= 0:
            raise ValueError("价位必须为正")
        return self


class BookTarget(BookModel):
    providerId: str = Field(min_length=1)
    upstreamSource: str = Field(min_length=1)


class OrderBook(BookModel):
    contractVersion: Literal[1]
    capability: Literal["ORDER_BOOK"]
    symbol: str = Field(min_length=1)
    market: Literal["CN", "HK", "US"]
    venue: str | None
    coverage: Literal["venue", "consolidated", "unknown"]
    priceCurrency: Literal["CNY", "HKD", "USD", "unknown"]
    quantityUnit: Literal["share", "contract", "unknown"]
    requestedDepth: int = Field(ge=1, le=100)
    sourceDepthLimit: int | None = Field(ge=1, le=100)
    depthDegraded: bool
    bids: list[Level] = Field(max_length=100)
    asks: list[Level] = Field(max_length=100)
    sourceEventTime: str | None
    receivedAt: str
    sequence: int | None = Field(ge=0, le=9007199254740991)
    target: BookTarget
    adapterRevision: str = Field(min_length=1)
    dataNature: Literal["realtime", "delayed", "sampled", "unknown"]
    state: Literal["valid", "stale"]
    continuity: Literal["verified", "unknown"]

    @model_validator(mode="after")
    def invariants(self):
        for raw in (self.sourceEventTime, self.receivedAt):
            if raw is not None:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})", raw):
                    raise ValueError("盘口时间必须为带时区的 ISO 时间")
                if datetime.fromisoformat(raw.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("盘口时间必须携带时区")
        limit = min(self.requestedDepth, self.sourceDepthLimit or self.requestedDepth)
        for rows, descending in ((self.bids, True), (self.asks, False)):
            prices = [Decimal(row.price) for row in rows]
            if len(prices) > limit or len(set(prices)) != len(prices) or prices != sorted(prices, reverse=descending):
                raise ValueError("盘口深度、排序或价位重复")
        degraded = self.sourceDepthLimit is not None and self.sourceDepthLimit < self.requestedDepth
        if self.depthDegraded != degraded:
            raise ValueError("深度降级声明不一致")
        if self.continuity == "verified" and (self.sequence is None or self.state != "valid"):
            raise ValueError("不能证明盘口连续性")
        return self


def validate_book(value: object) -> OrderBook:
    value = value.model_dump() if isinstance(value, OrderBook) else value
    return OrderBook.model_validate(value)


def correlate_book(request: OrderBookRequest, book: OrderBook) -> OrderBook:
    if (request.symbol, request.market, request.depth) != (book.symbol, book.market, book.requestedDepth):
        raise ValueError("盘口请求与结果身份不一致")
    if book.depthDegraded and not request.allowDepthDegradation:
        raise ValueError("调用方未允许深度降级")
    return book


def validate_book_binding(request: OrderBookRequest, key: Mapping, target: Mapping) -> None:
    if key.get("kind") != "data" or key.get("capability") != "ORDER_BOOK" or key.get("market") != request.market:
        raise ValueError("盘口请求与精确能力绑定不一致")


def correlate_bound_book(request: OrderBookRequest, book: OrderBook, target: Mapping) -> OrderBook:
    if (book.target.providerId, book.target.upstreamSource) != (target.get("providerId"), target.get("upstreamSource")):
        raise ValueError("盘口结果与固定来源目标不一致")
    return correlate_book(request, book)
