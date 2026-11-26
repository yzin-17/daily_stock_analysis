"""按日官方停复牌材料重建请求时刻的执行状态，其他制度仍须独立合格。"""

from datetime import timedelta, timezone
from typing import Annotated, Literal
from pydantic import Field, field_validator
from .thesis_ledger_status_capabilities_v1 import Model, Day, instant
from .thesis_ledger_historical_execution import (
    Fact, ExecutionIdentity, ExactPrices, Unlimited, Coverage, ExecutionContext, SampledExecutionContext,
)
from .thesis_ledger_historical_trading import OriginalEvidence, ReconstructionEvidence, Listing, Session, Refs
from .thesis_ledger_sse_halt_query import read_halt_rows, halt_interval


class DailyTradingMaterial(Model):
    day: Day
    fact: ExecutionIdentity
    evidence: list[OriginalEvidence] = Field(min_length=1, max_length=30)
    listing: Listing
    sessions: list[Session] = Field(min_length=1, max_length=8)
    pages: list[ReconstructionEvidence] = Field(min_length=1, max_length=32)

    @field_validator('version', mode='before', check_fields=False)
    @classmethod
    def version_integer(cls, value):
        if type(value) is not int:
            raise ValueError('按日重建材料版本必须为整数')
        return value


class DailyExecutionMaterial(DailyTradingMaterial):
    version: Literal[1]
    fact: Fact
    priceRule: Annotated[ExactPrices | Unlimited, Field(discriminator='kind')]
    ruleCoverage: Coverage
    ruleEvidenceRefs: Refs


class SampledDailyExecutionMaterial(DailyTradingMaterial):
    version: Literal[2]
    identityEvidenceRefs: Refs


def sampled_daily_material(value):
    if value.get('version') == 2:
        return SampledDailyExecutionMaterial.model_validate(value)
    if type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('按日材料版本无效')
    if set(value['fact']) - set(Fact.model_fields):
        raise ValueError('历史身份包含未知字段')
    identity = [e['id'] for e in value['evidence'] if e['kind'] == 'snapshot'] or value['ruleEvidenceRefs']
    required = set(identity) | set(value['listing']['evidenceRefs'])
    for session in value['sessions']:
        required.update(session['evidenceRefs'])
    return SampledDailyExecutionMaterial.model_validate({
        'version': 2, 'day': value['day'],
        'fact': {k: value['fact'][k] for k in ExecutionIdentity.model_fields},
        'identityEvidenceRefs': identity,
        'evidence': [e for e in value['evidence'] if e['id'] in required],
        'listing': value['listing'], 'sessions': value['sessions'], 'pages': value['pages'],
    })


def reconstruct_context(material, as_of, read_original):
    at = instant(as_of)
    day = at.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    if material.day != day:
        raise ValueError('按日状态材料与模拟日期不符')
    # 历史分钟以区间结束标记；精确收盘端点仍需要该分钟的证券执行资料。
    # 这只证明证券状态，是否接受新订单仍由消费者的交易会话门禁决定。
    sessions = [s for s in material.sessions if instant(s.startAt) <= at <= instant(s.endAt)]
    if len(sessions) != 1:
        raise ValueError('缺少唯一的目标交易会话')
    for evidence in [*material.evidence, *material.pages]:
        read_original(evidence.sha256)
    rows, total = read_halt_rows(material.pages, material.fact.symbol, day, read_original)
    halts = []
    for row in rows:
        interval = halt_interval(row, day)
        if interval is None or instant(interval['startedAt']) > at:
            continue
        if instant(interval['endedAt']) > at:
            interval['endedAt'] = None
        halts.append(interval)
    active = any(h['endedAt'] is None for h in halts)
    ids = [page.id for page in material.pages]
    context = {
        'asOf': as_of, 'fact': material.fact.model_dump(),
        'evidence': [e.model_dump() for e in [*material.evidence, *material.pages]],
        'trading': {'state': 'suspended' if active else 'trading', 'intradayHalts': halts, 'evidenceRefs': ids,
            'basis': {'method': 'sse-fund-halts-v1', 'listing': material.listing.model_dump(),
                'session': sessions[0].model_dump(), 'query': {'symbol': material.fact.symbol,
                    'day': day, 'pages': len(material.pages), 'total': total, 'evidenceRefs': ids}}},
    }
    if isinstance(material, SampledDailyExecutionMaterial):
        return SampledExecutionContext.model_validate({**context, 'identityEvidenceRefs': material.identityEvidenceRefs})
    return ExecutionContext.model_validate({**context, 'priceRule': material.priceRule.model_dump(),
        'ruleCoverage': material.ruleCoverage.model_dump(), 'ruleEvidenceRefs': material.ruleEvidenceRefs})
