import pytest
from src.services.thesis_ledger_announcement_risk_parser import parse_risk_text
from src.services.thesis_ledger_announcement_status import library_status_pack, merge_status_day
from src.services.thesis_ledger_announcements import AnnouncementRepository, observation_clock
from tests.test_thesis_ledger_announcements import record


def test_explicit_date_only_and_explicit_percentage():
    text = '公司自2023年8月18日开市起撤销退市风险警示，股票交易价格的日涨跌幅限制由“5%”变更为“10%”。'
    facts = parse_risk_text(text, '2023-08-17')
    assert facts == [{"date": "2023-08-18", "state": "unknown",
                      "riskWarning": {"delisting": "none", "other": "unknown"},
                      "dailyPriceLimit": {"reference": "previousRawClose", "maxUpRatio": "0.1", "maxDownRatio": "0.1"}}]
    assert parse_risk_text('预计公司自2023年8月18日起实施其他风险警示。', '2023-08-17') == []
    assert parse_risk_text('公司自2023年8月16日起实施其他风险警示。', '2023-08-17') == []
    assert parse_risk_text('公司申请撤销退市风险警示，尚待批准。', '2023-08-17') == []


def test_new_company_risk_source_reads_downloaded_facts_without_inferring_normal(tmp_path):
    repo = AnnouncementRepository(tmp_path)
    raw = b'%PDF-fixture'
    facts = parse_risk_text('公司自2023年8月21日起实施其他风险警示。', '2023-08-20')
    repo.save({**record(), "sha256": repo.save_original(raw), "observedAt": observation_clock(),
               "downloadState": "downloaded", "parseState": "parsed", "riskDays": facts})
    pack = library_status_pack({"symbol": None, "days": [], "sourceDocuments": []}, '000881.SZ',
                               '2023-08-20', '2023-08-23', 'cn-stock-risk-price-limits', repo)
    assert pack['days'][0]['state'] == 'unknown'
    assert pack['days'][0]['riskWarning']['other'] == 'active'
    assert [d['date'] for d in pack['days']] == ['2023-08-21']
    assert pack['sourceDocuments'][0]['sha256'] == repo.save_original(raw)


def test_independent_dimensions_merge_but_contradictions_fail():
    base = {"date": "2023-08-21", "state": "trading", "sourceDocumentIds": ['a']}
    incoming = {**base, "state": "unknown", "sourceDocumentIds": ['b'],
                "riskWarning": {"delisting": "none", "other": "unknown"}}
    assert merge_status_day(base, incoming)['state'] == 'trading'
    with pytest.raises(ValueError, match="矛盾"):
        merge_status_day(base, {**incoming, "state": "suspended"})
