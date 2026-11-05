"""当前公开日期证据必须匹配原观察，不伪造精确披露或未来可用性。"""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from api.thesis_ledger import _fund_holdings_payload
from data_provider import fund_disclosure_reference as references
from data_provider.eastmoney_holdings_projection import holdings_result
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_fund_disclosure_evidence import publication_available_at
from src.services.thesis_ledger_fund_holdings_contract_v1 import HoldingsRequest, HoldingsResult
from tests.test_configured_market_routes import store, apply, no_admissions  # noqa: F401


def frame(*, symbol='161725.OF', period='2026-Q2', count=3):
    record = references._reference('161725.OF', '2026-Q2')
    rows = record.holdings[:count]
    result = pd.DataFrame({'股票代码': [row.symbol[:6] for row in rows],
        '股票名称': [row.name for row in rows],
        '占净值比例': [float(Decimal(row.weight) * 100) for row in rows],
        '季度': [f'{period[:4]}年{period[-1]}季度'] * len(rows)})
    return result, HoldingsRequest(symbol=symbol)


def project(**kwargs):
    values, request = frame(**kwargs)
    return holdings_result(values, request, 'efinance', 'eastmoney')


@pytest.mark.parametrize('count', [3, 10, 74])
def test_exact_current_reference_keeps_observation_weights_and_unknown_instant(count):
    value = project(count=count)
    wire = _fund_holdings_payload('161725.OF', value, 'efinance', False)
    proof = wire['disclosureEvidence']
    assert wire['disclosureDate'] is None
    assert proof['status'] == 'matched' and proof['precision'] == 'DATE_ONLY'
    assert proof['publishedOn'] == '2026-08-31'
    assert proof['matchedCount'] == count and proof['referenceCount'] == 74
    assert proof['holdingsDigest'] == wire['evidenceVersion']
    assert len(wire['holdings']) == count
    assert datetime.fromisoformat(proof['availableAt']) >= datetime.fromisoformat(proof['referenceObservedAt'])
    if count == 74:
        assert sum(row['weight'] for row in wire['holdings']) == pytest.approx(0.9477)
        assert len([row for row in wire['holdings'] if row['weight'] == 0]) == 54


@pytest.mark.parametrize('symbol,period', [('000001.OF', '2026-Q2'), ('161725.OF', '2026-Q3')])
def test_other_fund_or_period_never_adopts_reference(symbol, period):
    value = project(symbol=symbol, period=period)
    assert value.disclosure_evidence is None and value.disclosure_date is None
    assert 'disclosureEvidence' not in _fund_holdings_payload(symbol, value, 'efinance', False)


@pytest.mark.parametrize('change', ['weight', 'symbol', 'normalization'])
def test_known_reference_mismatch_keeps_observation_but_not_a_publication_claim(change):
    values, request = frame()
    if change == 'weight':
        values.loc[0, '占净值比例'] += 0.01
    elif change == 'symbol':
        values.loc[0, '股票代码'] = '600000'
    else:
        values['占净值比例'] *= 100 / values['占净值比例'].sum()
    value = holdings_result(values, request, 'efinance', 'eastmoney')
    proof = _fund_holdings_payload('161725.OF', value, 'efinance', False)['disclosureEvidence']
    assert proof['status'] == 'unavailable' and proof['reasonCode'] == 'holdings_mismatch'
    assert 'availableAt' not in proof and value.disclosure_date is None


def test_current_display_name_does_not_infer_or_replace_security_identity():
    values, request = frame()
    values.loc[0, '股票名称'] = '当前名称'
    value = holdings_result(values, request, 'efinance', 'eastmoney')
    assert value.disclosure_evidence.status == 'matched'
    assert value.holdings[0].name == '当前名称'


@pytest.mark.parametrize('provider,upstream', [('controlled', 'eastmoney'), ('efinance', 'other')])
def test_reference_does_not_change_provider_binding(provider, upstream):
    values, request = frame()
    value = holdings_result(values, request, provider, upstream)
    assert value.disclosure_evidence is None


@pytest.mark.parametrize('corruption', ['published', 'duplicate', 'missing'])
def test_registered_reference_failure_is_not_treated_as_absent(monkeypatch, tmp_path, corruption):
    values, request = frame()
    path = Path(references.__file__).parent / references.REFERENCE_FILES[('161725.OF', '2026-Q2')]
    record = json.loads(path.read_text())
    if corruption == 'published':
        record['publishedOn'] = 'not-a-date'
        values.loc[0, '占净值比例'] += 0.01  # 即使观察也失配，损坏不能隐藏。
    elif corruption == 'duplicate':
        record['holdings'].append(record['holdings'][0])
    changed = tmp_path / 'record.json'
    if corruption != 'missing':
        changed.write_text(json.dumps(record, ensure_ascii=False))
    monkeypatch.setitem(references.REFERENCE_FILES, ('161725.OF', '2026-Q2'), str(changed))
    with pytest.raises(CapabilityContractError):
        holdings_result(values, request, 'efinance', 'eastmoney')


@pytest.mark.parametrize('change', ['fund', 'period', 'digest', 'count', 'future', 'instant'])
def test_standard_result_rejects_unbound_or_future_reference(change):
    value = project()
    data = value.model_dump()
    proof = data['disclosure_evidence']
    if change == 'fund':
        proof['fund_symbol'] = '000001.OF'
    elif change == 'period':
        proof['report_period'] = '2026-Q1'
    elif change == 'digest':
        proof['holdings_digest'] = '0' * 64
    elif change == 'count':
        proof['matched_count'] += 1
    elif change == 'future':
        data['observed_at'] = '2026-09-01T00:00:00+00:00'
    else:
        data['disclosure_date'] = '2026-08-31T16:00:00+00:00'
    with pytest.raises(ValueError):
        HoldingsResult.model_validate(data)


def test_day_precision_uses_end_of_day_and_later_reference_check():
    boundary = publication_available_at('2026-08-31', '2026-08-31T07:00:00.000Z')
    assert datetime.fromisoformat(boundary) == datetime.fromisoformat('2026-08-31T16:00:00Z')
    later = publication_available_at('2026-08-31', '2026-10-21T00:00:00.000Z')
    assert datetime.fromisoformat(later) == datetime.fromisoformat('2026-10-21T00:00:00Z')


def test_actual_capability_factory_carries_publication_evidence_without_admission(monkeypatch, store):
    import akshare as ak
    import data_provider.akshare_fetcher as module
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, ThesisLedgerDataRequest
    values, _ = frame(count=10)
    calls = []

    def fetch(symbol, date):
        calls.append((symbol, date))
        return values

    monkeypatch.setattr(ak, 'fund_portfolio_hold_em', fetch)
    monkeypatch.setattr(module, 'datetime', SimpleNamespace(now=lambda *_args, **_kwargs: SimpleNamespace(year=2026)))
    key = {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': 'FUND_HOLDINGS'}
    apply(store, key, {'providerId': 'akshare', 'upstreamSource': 'eastmoney'})
    result = ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('FUND_HOLDINGS', '161725.OF'))
    assert result.value.disclosure_evidence.status == 'matched'
    assert result.value.disclosure_date is None and len(result.value.holdings) == 10
    assert calls == [('161725', '2026')]
    no_admissions(store)
