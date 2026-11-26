"""历史状态证据：原生快照与官方历史重建分别保留真实时钟。"""

from datetime import date, timedelta, timezone
from typing import Annotated, Literal
from pydantic import Field, field_validator
from .thesis_ledger_status_capabilities_v1 import Model, Text, Day, instant

Refs = Annotated[list[Text], Field(min_length=1, max_length=32)]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class OriginalEvidence(Model):
    id: Text
    kind: Literal['snapshot', 'rule']
    sha256: Digest
    reference: Text
    availableAt: str
    observedAt: str


class ReconstructionEvidence(Model):
    id: Text
    kind: Literal['reconstruction']
    sha256: Digest
    reference: Text
    observedAt: str
    httpStatus: Literal[200]


Evidence = Annotated[OriginalEvidence | ReconstructionEvidence, Field(discriminator='kind')]


class Halt(Model):
    startedAt: str
    endedAt: str | None


class Listing(Model):
    listedOn: Day
    delistedOn: Day | None
    evidenceRefs: Refs


class Session(Model):
    startAt: str
    endAt: str
    evidenceRefs: Refs

    @field_validator('startAt', 'endAt')
    @classmethod
    def clock(cls, value):
        import re
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('交易会话缺少明确时区')
        instant(value)
        return value


class HistoryQuery(Model):
    symbol: Text
    day: Day
    pages: int = Field(ge=1, le=256)
    total: int = Field(ge=0, le=100000)
    evidenceRefs: Refs


class ReconstructionBasis(Model):
    method: Literal['sse-fund-halts-v1']
    listing: Listing
    session: Session
    query: HistoryQuery


class SnapshotTrading(Model):
    state: Literal['trading', 'suspended']
    intradayHalts: list[Halt] = Field(max_length=256)
    evidenceRefs: Refs


class ReconstructedTrading(SnapshotTrading):
    basis: ReconstructionBasis


Trading = ReconstructedTrading | SnapshotTrading


def validate_trading(context, refs):
    """仅状态重建原文允许晚于历史时刻采集，其他证据保持原门禁。"""
    from urllib.parse import urlparse
    at = instant(context.asOf)
    for evidence in context.evidence:
        url = urlparse(evidence.reference)
        if not url.scheme or not url.netloc:
            raise ValueError('执行证据引用无效')
        if isinstance(evidence, ReconstructionEvidence):
            if instant(evidence.observedAt) < at:
                raise ValueError('历史重建查询不能早于所重建时刻')
        elif (instant(evidence.availableAt) > at
              or instant(evidence.observedAt) < instant(evidence.availableAt)):
            raise ValueError('执行证据未在模拟时刻可见或采集时钟倒置')
    trading = context.trading
    basis = getattr(trading, 'basis', None)
    expected = 'reconstruction' if basis else 'snapshot'
    def check(ids, kinds):
        if len(set(ids)) != len(ids) or any(i not in refs or refs[i].kind not in kinds for i in ids):
            raise ValueError('执行证据引用缺失或种类不符')
    check(trading.evidenceRefs, {expected})
    check(getattr(context, 'ruleEvidenceRefs', ()), {'rule'})
    if basis is None:
        if any(e.kind == 'reconstruction' for e in context.evidence):
            raise ValueError('历史重建证据缺少推导依据')
        return
    day = at.astimezone(timezone(timedelta(hours=8))).date()
    listing, session, query = basis.listing, basis.session, basis.query
    if (date.fromisoformat(listing.listedOn) > day or listing.delistedOn is not None
            and date.fromisoformat(listing.delistedOn) <= day):
        raise ValueError('目标证券不在上市存续期')
    start, end = instant(session.startAt), instant(session.endAt)
    if (not start <= at <= end or start.astimezone(timezone(timedelta(hours=8))).date() != day
            or end.astimezone(timezone(timedelta(hours=8))).date() != day):
        raise ValueError('目标时刻不在交易会话')
    if (query.symbol != context.fact.symbol or query.day != day.isoformat()
            or set(query.evidenceRefs) != set(trading.evidenceRefs)
            or query.pages != len(query.evidenceRefs)):
        raise ValueError('历史查询与证券、日期或分页不符')
    if context.fact.instrumentType != 'ETF' or not context.fact.symbol.endswith('.SH'):
        raise ValueError('基金停复牌重建只适用于上交所 ETF')
    check(listing.evidenceRefs, {'snapshot', 'rule'})
    check(session.evidenceRefs, {'snapshot', 'rule'})
    active = any(instant(h.startedAt) <= at and (h.endedAt is None or at < instant(h.endedAt))
                 for h in trading.intradayHalts)
    if trading.state != ('suspended' if active else 'trading'):
        raise ValueError('重建状态与停牌区间矛盾')
