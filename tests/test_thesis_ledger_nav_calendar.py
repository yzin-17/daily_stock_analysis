"""研究净值日期、缺日不交易、独立披露工作日与尾部预算的正负例。"""

from dataclasses import replace, FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json

import pytest

from data_provider.eastmoney_fund_nav import ENDPOINT, READER_REVISION
from data_provider.eastmoney_nav_evidence import (
    NavRawEvidence, NavRawPage, canonical_json, evidence_hash, hash_raw,
    native_record_fragments, parse_nav_record,
)
from src.services import thesis_ledger_nav_calendar as producer
from src.services import thesis_ledger_nav_rules as rules


NOW = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)
AS_OF = '2026-10-01T02:00:00Z'
DAYS = ['2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-12',
        '2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18', '2026-09-21']


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def workdays(start, end, cutoff):
    days = []
    current = datetime.fromisoformat(start)
    while current.date().isoformat() <= end:
        if current.weekday() < 5:
            days.append(current.date().isoformat())
        current += timedelta(days=1)
    return tuple(days), {'calendar': 'XSHG', 'version': 'controlled-calendar',
                         'sourceHash': 'a' * 64, 'availableAt': '2026-03-10T03:24:37Z'}


@pytest.fixture(autouse=True)
def controlled_clock_and_calendar(monkeypatch):
    monkeypatch.setattr(producer, 'datetime', Clock)
    monkeypatch.setattr(rules, 'datetime', Clock)
    monkeypatch.setattr(producer, 'read_nav_research_workdays', workdays)


def source(days=DAYS):
    items = [{'FSRQ': day, 'DWJZ': '1.2300'} for day in reversed(days)]
    raw = canonical_json({'Success': True, 'ErrCode': 0, 'ErrorCode': '0',
                          'TotalCount': len(items), 'Datas': items})
    page = NavRawPage(1, raw, hash_raw(raw))
    records = tuple(parse_nav_record(item, 1, fragment)
                    for item, fragment in zip(items, native_record_fragments(raw)))
    return NavRawEvidence('161725', ENDPOINT, READER_REVISION, '2026-09-30T15:00:00Z',
                          len(items), (page,), records,
                          evidence_hash('161725', ENDPOINT, READER_REVISION, [page]))


def rule():
    raw = canonical_json({
        'schemaVersion': 'nav-research-default-v1', 'symbol': '161725.OF', 'fundType': 'domestic',
        'delayWorkdays': 1, 'applicableRange': {'startDate': '2026-09-01', 'endDate': '2026-09-30'},
        'configuredAt': '2026-10-01T00:00:00Z', 'decision': '普通 T+1 研究口径',
    })
    return rules.build_domestic_nav_rule(raw, evidence_ref='research-config://test', data_as_of=AS_OF)


def decision(**changes):
    return canonical_json({'schemaVersion': 'nav-research-calendar-decision-v1',
                           'symbol': '161725.OF', 'basis': 'nav-dates-xshg-intersection-v1',
                           'configuredAt': '2026-10-01T00:00:00Z',
                           'decision': '用户确认净值日期与沪深交集，缺日不交易，暂停/限额不模拟', **changes})


def build(**changes):
    values = {'source': source(), 'rule_evidence': rule(), 'decision_raw': decision(),
              'mode': 'research-assumption', 'start': '2026-09-09', 'end': '2026-09-14',
              'warmup_periods': 2, 'tail_trading_days': 2, 'data_as_of': AS_OF}
    values.update(changes)
    return producer.build_nav_research_calendar(**values)


def test_three_sets_missing_date_and_non_trading_valuation():
    packet = build()
    calendar = packet.calendar
    assert calendar['coverage'] == {'startDate': '2026-09-07', 'endDate': '2026-09-16', 'complete': True}
    assert '2026-09-12' in calendar['valuationDates']
    assert '2026-09-12' not in calendar['tradingDates']
    assert '2026-09-11' not in calendar['valuationDates']
    assert '2026-09-11' not in calendar['tradingDates']
    assert '2026-09-11' in calendar['disclosureWorkDates']
    assert calendar['contentHash'] == hash_raw(packet.calendar_raw)
    proof = json.loads(packet.assumption_raw)
    assert proof['sourceHash'] == source().content_hash
    assert proof['ruleHash'] == rule().rule['contentHash']
    assert proof['decisionHash'] == hash_raw(packet.decision_raw)
    assert proof['missingDate'] == 'untradable'
    assert calendar['version'].endswith(hash_raw(packet.assumption_raw))
    assert calendar['availableAt'] == NOW.isoformat()
    assert 'sourcePublishedAt' not in packet.assumption_raw
    calendar['tradingDates'].clear()
    assert packet.calendar['tradingDates']
    with pytest.raises(FrozenInstanceError):
        packet.calendar_raw = '{}'


def test_lookahead_stops_at_source_day_and_retains_full_tail_guard(monkeypatch):
    checked_ends = []

    def bounded_workdays(start, end, cutoff):
        checked_ends.append(end)
        if end > '2026-12-31':
            raise ValueError('已核验日历末日之后')
        return workdays(start, end, cutoff)

    monkeypatch.setattr(producer, 'read_nav_research_workdays', bounded_workdays)
    days = [*DAYS, '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-28']
    packet = build(source=source(days), end='2026-09-21', tail_trading_days=4)
    assert checked_ends == ['2026-09-30']
    assert packet.calendar['coverage']['endDate'] == '2026-09-28'
    with pytest.raises(ValueError, match='尾部净值不足'):
        build(source=source(days), end='2026-09-21', tail_trading_days=5)


@pytest.mark.parametrize('changes', [
    {'mode': 'strict-publication'}, {'warmup_periods': 0}, {'warmup_periods': True},
    {'tail_trading_days': 0}, {'tail_trading_days': 105}, {'tail_trading_days': True},
    {'warmup_periods': 99}, {'tail_trading_days': 10},
    {'start': '2026-09-22', 'end': '2026-09-23'},
    {'start': '2026-09-14', 'end': '2026-09-09'},
])
def test_invalid_mode_ranges_or_insufficient_budget(changes):
    with pytest.raises(ValueError):
        build(**changes)


@pytest.mark.parametrize('changes', [
    {'symbol': '118001.OF'}, {'basis': 'real-fund-calendar'}, {'decision': ''},
    {'configuredAt': '2026-10-01T03:00:00Z'}, {'extra': True},
])
def test_explicit_decision_identity_and_time(changes):
    with pytest.raises(ValueError):
        build(decision_raw=decision(**changes))


@pytest.mark.parametrize('mutation', [
    lambda s: replace(s, content_hash='0' * 64),
    lambda s: replace(s, total_count=s.total_count + 1),
    lambda s: replace(s, pages=()),
    lambda s: replace(s, pages=(replace(s.pages[0], raw_response=s.pages[0].raw_response + ' '),)),
    lambda s: replace(s, records=(replace(s.records[0], unit_nav='9'), *s.records[1:])),
    lambda s: replace(s, captured_at='2026-10-01T03:00:00Z'),
])
def test_source_integrity_and_future_capture(mutation):
    with pytest.raises(ValueError):
        build(source=mutation(source()))


def test_duplicate_dates_and_future_nav_rejected():
    for days in [[*DAYS, DAYS[0]], [*DAYS, '2026-10-02']]:
        with pytest.raises(ValueError):
            build(source=source(days))


def test_rule_document_tamper_and_warmup_range_rejected():
    with pytest.raises(ValueError):
        build(rule_evidence=replace(rule(), document_raw=b'tampered'))
    payload = json.loads(rule().rule_raw)
    payload['applicableRange']['startDate'] = '2026-09-08'
    with pytest.raises(ValueError):
        build(rule_evidence=replace(rule(), rule_raw=canonical_json(payload)))


def test_missing_disclosure_days_rejected(monkeypatch):
    monkeypatch.setattr(producer, 'read_nav_research_workdays', lambda *args: workdays('2026-09-07', '2026-09-14', None))
    packet = rule()
    payload = json.loads(packet.rule_raw)
    payload.update({'delayWorkdays': 2, 'fundType': 'qdii', 'basis': 'verified-fund-rule'})
    with pytest.raises(ValueError, match='披露工作日尾部'):
        build(tail_trading_days=1, end='2026-09-12',
              rule_evidence=replace(packet, rule_raw=canonical_json(payload)))


def test_future_visibility_rejected_even_with_processing_tail():
    packet = rule()
    payload = json.loads(packet.rule_raw)
    payload.update({'delayWorkdays': 2, 'fundType': 'qdii', 'basis': 'verified-fund-rule'})
    # 搜索止于采集日，未来披露需要的工作日不会进入冻结日历。
    with pytest.raises(ValueError, match='披露工作日尾部不足'):
        build(source=source([*DAYS, '2026-09-29', '2026-09-30']), start='2026-09-29',
              end='2026-09-29', tail_trading_days=1,
              rule_evidence=replace(packet, rule_raw=canonical_json(payload)))


def test_unverified_calendar_release_rejected(monkeypatch):
    from src.services import thesis_ledger_nav_calendar_source as calendar_source
    monkeypatch.setattr(calendar_source, 'verified_calendar_available_at', lambda *args: None)
    with pytest.raises(ValueError, match='制品未核验'):
        calendar_source.read_nav_research_workdays('2026-09-01', '2026-09-30', NOW)


def test_future_production_rejected():
    with pytest.raises(ValueError, match='生产晚于'):
        build(data_as_of='2026-10-01T00:30:00Z', rule_evidence=replace(rule(), captured_at='2026-10-01T00:00:00Z'))
