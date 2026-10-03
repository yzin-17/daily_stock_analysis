"""从精确净值快照生产显式研究日期；缺日不交易，不声明真实暂停完整。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo

from data_provider.eastmoney_fund_nav import ENDPOINT, READER_REVISION
from data_provider.eastmoney_nav_evidence import (
    NavRawEvidence, canonical_json, decode_source_json, hash_raw, validate_nav_raw_evidence,
)
from src.services.thesis_ledger_nav_calendar_source import read_nav_research_workdays
from src.services.thesis_ledger_nav_rules import NavResearchRuleEvidence, _instant, _range


@dataclass(frozen=True)
class NavResearchCalendarEvidence:
    calendar_raw: str
    assumption_raw: str
    decision_raw: str
    captured_at: str

    @property
    def calendar(self) -> dict:
        proof = hash_raw(self.assumption_raw)
        return {**decode_source_json(self.calendar_raw), 'contentHash': hash_raw(self.calendar_raw),
                'evidenceRef': f'research-config://nav-calendar/{proof}', 'availableAt': self.captured_at}


def build_nav_research_calendar(
    source: NavRawEvidence, rule_evidence: NavResearchRuleEvidence, decision_raw: str, *,
    mode: str, start: str, end: str, warmup_periods: int, tail_trading_days: int, data_as_of: str,
) -> NavResearchCalendarEvidence:
    cutoff = _instant(data_as_of)
    _range(start, end)
    if (mode != 'research-assumption' or type(warmup_periods) is not int
            or not 1 <= warmup_periods <= 100000 or type(tail_trading_days) is not int
            or not 1 <= tail_trading_days <= 104):
        raise ValueError('研究日期模式或预热/尾部预算无效')
    if not isinstance(decision_raw, str) or len(decision_raw.encode('utf-8')) > 65536:
        raise ValueError('研究日期决策原文无效')
    decision = decode_source_json(decision_raw)
    if (not isinstance(decision, dict) or set(decision) != {
        'schemaVersion', 'symbol', 'basis', 'configuredAt', 'decision',
    } or decision['schemaVersion'] != 'nav-research-calendar-decision-v1'
            or decision['basis'] != 'nav-dates-xshg-intersection-v1'
            or not isinstance(decision['decision'], str) or not decision['decision'].strip()
            or _instant(decision['configuredAt']) > cutoff):
        raise ValueError('研究日期必须绑定显式用户决策及有效配置时刻')
    validate_nav_raw_evidence(source)
    rule = rule_evidence.rule
    symbol = f'{source.fund_code}.OF'
    if (source.endpoint != ENDPOINT or source.reader_revision != READER_REVISION
            or rule['symbol'] != symbol or decision['symbol'] != symbol
            or rule['documentHash'] != sha256(rule_evidence.document_raw).hexdigest()
            or type(rule['delayWorkdays']) is not int or not 1 <= rule['delayWorkdays'] <= 104):
        raise ValueError('研究日期来源、规则标的或原文身份不符')
    captured_source = _instant(source.captured_at)
    for instant in [captured_source, _instant(rule_evidence.captured_at), _instant(rule['configuredAt'])]:
        if instant > cutoff:
            raise ValueError('研究日期来源或规则晚于冻结时点')
    source_day = captured_source.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    dates = sorted(record.valuation_date for record in source.records)
    if dates[-1] > source_day or end > cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat():
        raise ValueError('研究日期包含未来净值或运行区间')
    interval = rule['applicableRange']
    preceding = [day for day in dates if interval['startDate'] <= day < start]
    execution = [day for day in dates if start <= day <= end]
    if not execution or len(preceding) < warmup_periods or end > interval['endDate']:
        raise ValueError('研究净值执行、预热或规则范围不足')
    first = preceding[-warmup_periods]
    # 搜索预算不是必须覆盖的未来日期；只查已采集事实所在时点以内的日历。
    horizon = min((datetime.fromisoformat(end) + timedelta(days=104)).date().isoformat(), source_day)
    workdays, release = read_nav_research_workdays(first, horizon, cutoff)
    workday_set = set(workdays)
    processing = [day for day in dates if first <= day <= horizon and day in workday_set]
    tail = [day for day in processing if day > end]
    if len(tail) < tail_trading_days or not any(start <= day <= end for day in processing):
        raise ValueError('研究申赎日期或确认/结算尾部净值不足')
    disclosure = []
    for day in [*preceding[-warmup_periods:], *execution]:
        following = [workday for workday in workdays if workday > day]
        if len(following) < rule['delayWorkdays']:
            raise ValueError('研究披露工作日尾部不足')
        disclosed = following[rule['delayWorkdays'] - 1]
        visible = datetime.fromisoformat(disclosed).replace(tzinfo=ZoneInfo('Asia/Shanghai')) + timedelta(days=1)
        if visible > cutoff:
            raise ValueError('研究净值在冻结时点尚不可见')
        disclosure.append(disclosed)
    last = max(tail[tail_trading_days - 1], max(disclosure))
    assumption_raw = canonical_json({
        'schemaVersion': 'nav-research-calendar-assumption-v1', 'symbol': symbol,
        'decisionHash': hash_raw(decision_raw), 'sourceHash': source.content_hash,
        'sourceRevision': source.reader_revision, 'sourceCapturedAt': source.captured_at,
        'ruleHash': rule['contentHash'], 'exchangeCalendar': release,
        'valuationBasis': 'source-nav-dates', 'processingBasis': 'nav-dates-xshg-intersection',
        'disclosureBasis': 'xshg-workdays', 'missingDate': 'untradable',
        'limitations': ['historical-suspension', 'subscription-limits', 'investor-channel-differences'],
    })
    calendar_raw = canonical_json({
        'symbol': symbol, 'market': 'CN', 'timezone': 'Asia/Shanghai',
        'version': f'nav-research-calendar-v1:{hash_raw(assumption_raw)}',
        'coverage': {'startDate': first, 'endDate': last, 'complete': True},
        'valuationDates': [day for day in dates if first <= day <= last],
        'tradingDates': [day for day in processing if day <= last],
        'disclosureWorkDates': [day for day in workdays if day <= last],
    })
    captured = datetime.now(timezone.utc)
    if captured > cutoff:
        raise ValueError('研究日期生产晚于冻结时点')
    return NavResearchCalendarEvidence(calendar_raw, assumption_raw, decision_raw, captured.isoformat())
