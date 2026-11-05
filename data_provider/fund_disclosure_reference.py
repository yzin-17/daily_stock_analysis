"""对已核对的公开参考逐项匹配；不请求网络，不补齐或归一 SDK 观察。"""

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from src.services.thesis_ledger_fund_disclosure_evidence import (
    CLOCK, DisclosureMatched, DisclosureUnavailable, PublicationSource, publication_available_at,
    validate_publication_reference,
)

REFERENCE_FILES = {
    ('161725.OF', '2026-Q2'): 'fund_disclosures/161725-2026-q2.json',
    ('000001.OF', '2026-Q3'): 'fund_disclosures/000001-2026-q3.json',
}


class _ReferenceHolding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, populate_by_name=True,
                              alias_generator=to_camel)
    symbol: str = Field(pattern=r'^\d{6}\.(SH|SZ|BJ)$')
    name: str = Field(min_length=1)
    weight: str = Field(pattern=r'^(?:0(?:\.\d{1,18})?|1(?:\.0{1,18})?)$')
    printed_page: int = Field(ge=1)


class _Reference(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, populate_by_name=True,
                              alias_generator=to_camel)
    format: Literal['fund-publication-reference-v1']
    fund_symbol: str = Field(pattern=r'^\d{6}\.OF$')
    report_period: str = Field(pattern=r'^[2-9]\d{3}-Q[1-4]$')
    report_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    issuer: str = Field(min_length=1)
    published_on: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    precision: Literal['DATE_ONLY']
    timezone: Literal['Asia/Shanghai']
    reference_observed_at: str = Field(pattern=CLOCK)
    sources: tuple[PublicationSource, ...]
    holdings: tuple[_ReferenceHolding, ...] = Field(min_length=1)
    qualification: str = Field(min_length=1)

    @field_validator('report_date')
    @classmethod
    def report_day(cls, value):
        from datetime import date
        date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def integrity(self):
        if len({row.symbol for row in self.holdings}) != len(self.holdings):
            raise ValueError('披露参考证券重复')
        if sum(Decimal(row.weight) for row in self.holdings) > 1:
            raise ValueError('披露参考权重超过 1')
        month = int(self.report_period[-1]) * 3
        import calendar
        expected = f'{self.report_period[:4]}-{month:02}-{calendar.monthrange(int(self.report_period[:4]), month)[1]:02}'
        if self.report_date != expected:
            raise ValueError('披露参考报告日与季度不匹配')
        validate_publication_reference(self.report_period, self.published_on,
                                       self.reference_observed_at, self.sources)
        return self


def _reference(symbol, report_period):
    selected = REFERENCE_FILES.get((symbol, report_period))
    if selected is None:
        return None
    # 已登记但文件损坏/缺失必须失败，不能当作没有参考。
    try:
        text = (Path(__file__).parent / selected).read_text(encoding='utf-8')
    except OSError as error:
        raise ValueError('已登记披露参考不可读取') from error
    record = _Reference.model_validate_json(text)
    if (record.fund_symbol, record.report_period) != (symbol, report_period):
        raise ValueError('披露参考登记身份不匹配')
    return record


def disclosure_for_observation(symbol, report_period, holdings, digest, provider, upstream):
    if provider not in {'efinance', 'akshare'} or upstream != 'eastmoney':
        return None
    record = _reference(symbol, report_period)
    if record is None:
        return None
    version = hashlib.sha256(json.dumps(record.model_dump(mode='json', by_alias=True),
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    weights = {row.symbol: Decimal(row.weight) for row in record.holdings}
    for holding in holdings:
        if holding.symbol not in weights or abs(Decimal(str(holding.weight)) - weights[holding.symbol]) > Decimal('1e-12'):
            return DisclosureUnavailable(fund_symbol=symbol, report_period=report_period, reference_version=version)
    return DisclosureMatched(
        fund_symbol=symbol, report_period=report_period, issuer=record.issuer,
        published_on=record.published_on, reference_observed_at=record.reference_observed_at,
        available_at=publication_available_at(record.published_on, record.reference_observed_at),
        reference_version=version, holdings_digest=digest, matched_count=len(holdings),
        reference_count=len(record.holdings), sources=record.sources,
    )
