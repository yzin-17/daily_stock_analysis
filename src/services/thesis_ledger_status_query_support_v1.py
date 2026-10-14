"""逐能力的参数资格；由来源绑定拥有，不声称本次数据完整。"""

from typing import Annotated, Literal
from pydantic import Field, TypeAdapter, field_validator, model_validator
from .thesis_ledger_status_capabilities_v1 import Model


class TradingSupport(Model):
    capability: Literal["DAILY_TRADING_STATUS"] = "DAILY_TRADING_STATUS"
    granularity: Literal["day"] = "day"
    maximumWindowDays: int = Field(ge=1)


class RiskSupport(Model):
    capability: Literal["RISK_WARNING"] = "RISK_WARNING"
    detailKinds: list[Literal["coarseST", "riskCategories"]] = Field(min_length=1)
    riskCategories: list[Literal["delisting", "other"]] = Field(max_length=2)

    @field_validator("detailKinds", "riskCategories")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("来源参数资格重复")
        return values

    @model_validator(mode="after")
    def precision(self):
        if ("riskCategories" in self.detailKinds) != bool(self.riskCategories):
            raise ValueError("风险参数资格与类别字段不一致")
        return self


class PriceSupport(Model):
    capability: Literal["DAILY_PRICE_RULE"] = "DAILY_PRICE_RULE"
    representations: list[Literal["rule", "exactPrices"]] = Field(min_length=1)

    @field_validator("representations")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("价格参数资格重复")
        return values


class DatesSupport(Model):
    capability: Literal["SECURITY_BASIC_DATES"] = "SECURITY_BASIC_DATES"
    fields: list[Literal["ipoDate", "outDate"]] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("基础日期参数资格重复")
        return values


class PhaseSupport(Model):
    capability: Literal["LISTING_PHASE"] = "LISTING_PHASE"
    phases: list[Literal["notListed", "initialListing", "listed", "delistingPeriod", "delisted"]] = Field(min_length=1)

    @field_validator("phases")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("上市阶段参数资格重复")
        return values


StatusQuerySupport = Annotated[TradingSupport | RiskSupport | PriceSupport | DatesSupport | PhaseSupport, Field(discriminator="capability")]
SUPPORT = TypeAdapter(StatusQuerySupport)
