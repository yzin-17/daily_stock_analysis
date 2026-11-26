"""盘中历史执行合同；不从日线或当前证券身份推导可交易性。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BeforeValidator, Field, field_validator, model_validator
from .thesis_ledger_status_capabilities_v1 import Model as StatusModel, Text, Day, Target, instant, validator, validate_binding
from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_historical_trading import Evidence, Trading, validate_trading

CAPABILITY = 'HISTORICAL_EXECUTION_CONTEXT'
Amount = Annotated[str, Field(max_length=64, pattern=r'^(?:0|[1-9]\d*)(?:\.\d+)?$')]
Positive = Amount
Lag = Annotated[int, Field(ge=0, le=9007199254740991)]


def strict_true(value):
    if type(value) is not bool or value is not True:
        raise ValueError('执行覆盖声明必须明确为真')
    return value


Verified = Annotated[Literal[True], BeforeValidator(strict_true)]


class Model(StatusModel):
    @field_validator('contractVersion', 'buyDebitAfterTradingDays', check_fields=False, mode='before')
    @classmethod
    def integer_literals(cls, value):
        if type(value) is not int:
            raise ValueError('执行合同版本及结算日数必须为整数')
        return value

    @field_validator('asOf', 'occurredAt', check_fields=False)
    @classmethod
    def execution_clock(cls, value):
        import re
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('执行时点缺少明确时区')
        instant(value)
        return value


class Key(Model):
    kind: Literal['data']
    market: Literal['CN']
    assetType: Literal['STOCK', 'ETF']
    capability: Literal['HISTORICAL_EXECUTION_CONTEXT']


class ExecutionRequest(Model):
    contractVersion: Literal[1]
    consumer: Literal['thesis-ledger']
    requestId: Text
    routeKey: Key
    routeTarget: Target
    policyRevision: int = Field(ge=1, le=9007199254740991)
    symbol: Annotated[str, Field(pattern=r'^\d{6}\.(SH|SZ|BJ)$')]
    asOf: str
    purpose: Literal['full', 'sampled'] = 'full'

    @property
    def range(self):
        day = instant(self.asOf).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        return Window(start=day, end=day)

    @model_validator(mode='after')
    def clock(self):
        instant(self.asOf)
        if instant(self.asOf).utcoffset() is None:
            raise ValueError('执行时点缺少时区')
        return self


class Window(Model):
    start: Day
    end: Day


class Price(Model):
    reference: Literal['previousClose']
    maxUpRatio: Amount | None
    maxDownRatio: Amount | None


class PositionSettlement(Model):
    sellableAfterTradingDays: Lag


class CashSettlement(Model):
    buyDebitAfterTradingDays: Literal[0]
    sellCreditAfterTradingDays: Lag


class Charge(Model):
    code: Text
    side: Literal['buy', 'sell', 'both']
    rate: Amount
    minimum: Amount | None


class Rules(Model):
    status: Literal['supported']
    version: Text
    range: Window
    price: Price
    positionSettlement: PositionSettlement
    cashSettlement: CashSettlement
    statutoryCharges: list[Charge] = Field(max_length=64)


class ExecutionIdentity(Model):
    symbol: Text
    market: Literal['CN']
    instrumentType: Literal['STOCK', 'ETF']
    currency: Literal['CNY']
    lotSize: Positive
    tickSize: Positive
    provider: Text
    providerRevision: Text
    occurredAt: str
    availableAt: str


class Fact(ExecutionIdentity):
    tradable: Verified
    executionRules: Rules


class ExactPrices(Model):
    kind: Literal['exact']
    currency: Literal['CNY']
    lower: Positive
    upper: Positive


class Unlimited(Model):
    kind: Literal['unlimited']
    reason: Text


class Coverage(Model):
    quantity: Verified
    price: Verified
    positionSettlement: Verified
    cashSettlement: Verified
    statutoryCharges: Verified


class ExecutionContext(Model):
    asOf: str
    fact: Fact
    trading: Trading
    priceRule: Annotated[ExactPrices | Unlimited, Field(discriminator='kind')]
    ruleEvidenceRefs: list[Text] = Field(min_length=1, max_length=32)
    ruleCoverage: Coverage
    evidence: list[Evidence] = Field(min_length=2, max_length=32)

    @model_validator(mode='after')
    def qualified(self):
        at = instant(self.asOf)
        day = at.astimezone(timezone(timedelta(hours=8))).date().isoformat()
        fact, rule = self.fact, self.fact.executionRules
        if (at.utcoffset() is None or instant(fact.availableAt) > at or instant(fact.occurredAt) > at
                or not rule.range.start <= day <= rule.range.end
                or Decimal(fact.lotSize) <= 0 or Decimal(fact.tickSize) <= 0):
            raise ValueError('执行身份、可见时刻或制度覆盖不完整')
        if len({(charge.code, charge.side) for charge in rule.statutoryCharges}) != len(rule.statutoryCharges):
            raise ValueError('执行收费项目重复')
        refs = {e.id: e for e in self.evidence}
        if len(refs) != len(self.evidence):
            raise ValueError('执行证据标识重复')
        validate_trading(self, refs)
        for halt in self.trading.intradayHalts:
            if (instant(halt.startedAt) > at or halt.endedAt is not None
                    and not instant(halt.startedAt) < instant(halt.endedAt) <= at):
                raise ValueError('停牌区间含未来或倒置事实')
        if isinstance(self.priceRule, ExactPrices) and not 0 < Decimal(self.priceRule.lower) <= Decimal(self.priceRule.upper):
            raise ValueError('历史价格限制无效')
        return self


class SampledExecutionContext(Model):
    asOf: str
    fact: ExecutionIdentity
    trading: Trading
    identityEvidenceRefs: list[Text] = Field(min_length=1, max_length=32)
    evidence: list[Evidence] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def qualified(self):
        at = instant(self.asOf)
        if (instant(self.fact.availableAt) > at or instant(self.fact.occurredAt) > at
                or Decimal(self.fact.lotSize) <= 0 or Decimal(self.fact.tickSize) <= 0):
            raise ValueError('采样身份、可见时刻或交易单位无效')
        refs = {e.id: e for e in self.evidence}
        if (len(refs) != len(self.evidence) or len(set(self.identityEvidenceRefs)) != len(self.identityEvidenceRefs)
                or any(i not in refs or refs[i].kind not in ('snapshot', 'rule') for i in self.identityEvidenceRefs)):
            raise ValueError('采样身份证据缺失、重复或种类不符')
        validate_trading(self, refs)
        for halt in self.trading.intradayHalts:
            if (instant(halt.startedAt) > at or halt.endedAt is not None
                    and not instant(halt.startedAt) < instant(halt.endedAt) <= at):
                raise ValueError('停牌区间含未来或倒置事实')
        return self


def sampled_context(value):
    """旧完整归档仅投影所需字段，新采样归档按自身严格合同读取。"""
    if 'identityEvidenceRefs' in value:
        return SampledExecutionContext.model_validate(value)
    if set(value['fact']) - set(Fact.model_fields):
        raise ValueError('历史身份包含未知字段')
    identity = [e['id'] for e in value['evidence'] if e['kind'] == 'snapshot'] or value['ruleEvidenceRefs']
    trading = value['trading']
    required = set(identity) | set(trading['evidenceRefs'])
    if 'basis' in trading:
        required.update(trading['basis']['listing']['evidenceRefs'])
        required.update(trading['basis']['session']['evidenceRefs'])
    return SampledExecutionContext.model_validate({
        'asOf': value['asOf'], 'fact': {k: value['fact'][k] for k in ExecutionIdentity.model_fields},
        'trading': trading, 'identityEvidenceRefs': identity,
        'evidence': [e for e in value['evidence'] if e['id'] in required],
    })


class ExecutionResult(ExecutionRequest):
    context: ExecutionContext | SampledExecutionContext

    @model_validator(mode='after')
    def correlated(self):
        if (self.purpose == 'sampled') != isinstance(self.context, SampledExecutionContext):
            raise ValueError('历史执行用途与上下文不一致')
        fact = self.context.fact
        if (instant(self.context.asOf) != instant(self.asOf) or fact.symbol != self.symbol
                or fact.instrumentType != self.routeKey.assetType or fact.provider != self.routeTarget.providerId):
            raise ValueError('执行事实与精确请求不符')
        return self


def correlate(request, result, target):
    validate_binding(request, result.routeKey.model_dump(), target)
    if any(getattr(request, field) != getattr(result, field) for field in ExecutionRequest.model_fields):
        raise ValueError('执行结果身份、时点、代次或目标不一致')
    return result


CONTRACT = CapabilityContract(CAPABILITY, ExecutionRequest, validator(ExecutionResult),
                              validate_binding=validate_binding, correlate_result=correlate)
