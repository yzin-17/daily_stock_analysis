"""核查日期与法规生效期分开；未核查日期和历史请求不扩大来源范围。"""

from src.services.us_live_execution_rules import current_us_cash_rules
from src.services.thesis_ledger_us_live_rules_contract import UsLiveRules
from src.services.us_live_rules_refresh import checked_through, registered_source, IDENTITY, DOCUMENTS
from src.services.rule_monitor_runtime import RuleMonitorRuntime
from src.services.rule_monitor_store import RuleMonitorStore
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest


def test_reviewed_current_day_has_t1_and_explicit_taf_holiday():
    result = UsLiveRules.model_validate(current_us_cash_rules('2026-11-14', observed_at='2026-11-14T07:00:00Z'))
    assert result.verifiedThrough == '2026-11-14' and result.effectiveUntil == '2026-12-31'
    assert result.positionSettlementDays == result.sellCashSettlementDays == 1
    assert result.section31SellRate == '0.0000206'
    assert result.tafSellRatePerShare == result.tafSellMaximum == '0'
    assert result.priceIncrements.atOrAboveOne == '0.01' and result.priceIncrements.belowOne == '0.0001'
    assert len(result.sources) == 6 and result.exchanges == ['US_NYSE', 'US_NASDAQ', 'US_AMEX']


def test_known_effective_period_does_not_certify_future_checks_or_rewrite_history():
    for day in ['2026-11-13', '2026-11-16', '2026-12-31', '2027-01-01', '', None, '2026-11-140']:
        assert current_us_cash_rules(day, observed_at='2026-11-14T07:00:00Z') is None


def setup_monitor(tmp_path, fault=None):
    clock = datetime.fromisoformat('2026-11-16T15:00:00+00:00').timestamp()

    async def fetch(url, **kwargs):
        expected = next(digest for _, address, digest in DOCUMENTS if address == url)
        if fault == 'fetch':
            from src.services.rule_monitor_fetch import DocumentError
            raise DocumentError('document_unavailable')
        if fault == 'changed':
            expected = 'b' * 64
        return {'contentHash': expected, 'rawHash': expected,
                'snapshot': {'format': 'html', 'text': None if fault == 'snapshot' else '受控官方资料正文',
                             'reason': 'text_unavailable' if fault == 'snapshot' else None}}
    monitor = RuleMonitorRuntime(
        RuleMonitorStore(tmp_path / 'monitor.sqlite', clock=lambda: clock),
        registry=lambda: {IDENTITY: registered_source()}, fetch=fetch)
    context = SimpleNamespace(database_path=str(tmp_path / 'data.sqlite'), publish_guard=lambda: True)
    return monitor, context


def test_future_day_checks_enqueue_once_and_only_complete_unchanged_batch_advances(tmp_path):
    monitor, context = setup_monitor(tmp_path)
    assert checked_through('2026-11-16', context, monitor=monitor) is None
    assert checked_through('2026-11-16', context, monitor=monitor) is None
    assert monitor.run_pending_once()
    assert checked_through('2026-11-16', context, monitor=monitor) == '2026-11-16T15:00:00+00:00'
    assert not monitor.run_pending_once()
    assert checked_through('2026-11-17', context, monitor=monitor) is None
    assert not monitor.run_pending_once()
    assert not monitor.store.config()['enabled']  # 按需批次不会启用定时监测配置。


@pytest.mark.parametrize('fault', ['fetch', 'changed', 'snapshot'])
def test_unavailable_or_changed_material_keeps_execution_closed_without_automatic_retry(tmp_path, fault):
    monitor, context = setup_monitor(tmp_path, fault)
    assert checked_through('2026-11-16', context, monitor=monitor) is None
    assert monitor.run_pending_once()
    assert checked_through('2026-11-16', context, monitor=monitor) is None
    assert not monitor.run_pending_once()


def test_monitor_failure_does_not_break_normal_quote(monkeypatch):
    def broken(*args):
        raise OSError('受控监测故障')
    monkeypatch.setattr('src.services.us_live_rules_refresh.checked_through', broken)
    assert current_us_cash_rules('2026-11-16', observed_at='2026-11-16T15:00:00Z', context=object()) is None


def test_explicit_manual_recheck_restores_same_day_without_erasing_failed_job(tmp_path):
    monitor, context = setup_monitor(tmp_path, 'fetch')
    assert checked_through('2026-11-16', context, monitor=monitor) is None
    assert monitor.run_pending_once()
    failed = monitor.store.job()['id']
    healthy, _ = setup_monitor(tmp_path / 'healthy')
    monitor.fetch = healthy.fetch
    monitor.enqueue(str(uuid4()), [IDENTITY])
    assert monitor.run_pending_once()
    assert checked_through('2026-11-16', context, monitor=monitor) == '2026-11-16T15:00:00+00:00'
    assert monitor.store.job(failed)['counts']['unavailable'] == len(DOCUMENTS)
    assert not monitor.run_pending_once()
