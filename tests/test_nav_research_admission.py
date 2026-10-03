"""研究 NAV 准入只接受完整真实来源合同，并一次登记完整显式基金批次。"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from data_provider.eastmoney_fund_identity import identity_from_raw
from data_provider.eastmoney_nav_evidence import canonical_json, evidence_hash
from src.services import thesis_ledger_nav_calendar as calendar_module
from src.services.thesis_ledger_nav_rules import NavResearchRuleEvidence
from src.services.thesis_ledger_nav_research_admission import (
    NavResearchAdmissionError,
    prepare_nav_research_evidence,
    record_nav_research_admission,
)
from tests.test_thesis_ledger_nav_calendar import source as fixture_source


START = "2026-09-09"
END = "2026-09-14"
FUNDS = [
    {"symbol": "161725.OF", "fundType": "domestic"},
    {"symbol": "110011.OF", "fundType": "qdii"},
    {"symbol": "118001.OF", "fundType": "qdii"},
]
SOURCE_TYPES = {
    "161725": "指数型-股票",
    "110011": "QDII-混合偏股",
    "118001": "QDII-普通股票",
}


def _controlled_workdays(start, end, _cutoff):
    current = datetime.fromisoformat(start)
    days = []
    while current.date().isoformat() <= end:
        if current.weekday() < 5:
            days.append(current.date().isoformat())
        current += timedelta(days=1)
    return tuple(days), {
        "calendar": "XSHG",
        "version": "fixture-calendar-v1",
        "sourceHash": "a" * 64,
        "availableAt": "2026-09-01T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def controlled_calendar(monkeypatch):
    monkeypatch.setattr(calendar_module, "read_nav_research_workdays", _controlled_workdays)


def _identity(code):
    captured = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    raw = f'var reData={{datas:[["{code}","基金身份受控原文","{SOURCE_TYPES[code]}"]]}};'
    return identity_from_raw(raw, code, captured)


def _source(code):
    source = fixture_source()
    return replace(
        source,
        fund_code=code,
        content_hash=evidence_hash(code, source.endpoint, source.reader_revision, list(source.pages)),
        captured_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )


def _qdii_rule(symbol, *, start, end, data_as_of):
    document_raw = b"%PDF-controlled-rule-evidence"
    captured = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    delay = 2 if symbol == "118001.OF" else 1
    rule_raw = canonical_json({
        "id": f"fixture:{symbol}",
        "version": "fixture-qdii-rule-v1",
        "symbol": symbol,
        "fundType": "qdii",
        "applicableRange": {"startDate": start, "endDate": end},
        "delayWorkdays": delay,
        "basis": "verified-fund-rule",
        "evidenceRef": "fixture://verified-prospectus",
        "documentHash": hashlib.sha256(document_raw).hexdigest(),
        "configuredAt": captured,
    })
    return NavResearchRuleEvidence(
        rule_raw, document_raw, "fund-prospectus", captured, "fixture-reader-v1",
    )


def _readers():
    calls = {"identity": [], "nav": [], "qdii": []}

    def identity_reader(code):
        calls["identity"].append(code)
        return _identity(code)

    def nav_reader(code):
        calls["nav"].append(code)
        return _source(code)

    def qdii_reader(symbol, **kwargs):
        calls["qdii"].append((symbol, kwargs))
        return _qdii_rule(symbol, **kwargs)

    return calls, identity_reader, nav_reader, qdii_reader


def _prepare(tmp_path, *, funds=None, **overrides):
    calls, identity_reader, nav_reader, qdii_reader = _readers()
    values = {
        "funds": FUNDS if funds is None else funds,
        "research_mode": "research-assumption",
        "start": START,
        "end": END,
        "warmup_periods": 2,
        "tail_trading_days": 2,
        "research_decision": "用户确认按基金类型显式采用净值研究假设",
        "evidence_dir": tmp_path,
        "recorded_by": "n4-operator",
        "valid_hours": 24,
        "identity_reader": identity_reader,
        "nav_reader": nav_reader,
        "qdii_reader": qdii_reader,
    }
    values.update(overrides)
    return prepare_nav_research_evidence(**values), calls


class StoreStub:
    def __init__(self, existing=None, provider=None):
        self.existing = existing
        self.provider = provider or {
            "providerId": "efinance",
            "configured": True,
            "enabled": True,
            "requiresCredential": False,
            "tombstone": None,
        }
        self.record_calls = []

    def provider_registry(self):
        return [self.provider]

    def get_route_admission_v3(self, **_kwargs):
        return self.existing

    def record_route_admission_v3(self, **kwargs):
        self.record_calls.append(kwargs)
        return {**kwargs, "validUntil": kwargs["valid_until"]}


def test_batch_evidence_binds_all_funds_and_private_raw_artifacts(tmp_path):
    evidence, calls = _prepare(tmp_path)

    assert evidence["scopeSymbols"] == [fund["symbol"] for fund in FUNDS]
    assert evidence["scopeDateFrom"] == "2026-09-07"
    assert evidence["scopeDateTo"] == "2026-09-16"
    assert calls["identity"] == ["161725", "110011", "118001"]
    assert calls["nav"] == ["161725", "110011", "118001"]
    assert [item[0] for item in calls["qdii"]] == ["110011.OF", "118001.OF"]

    manifest_path = Path(evidence["manifestPath"])
    manifest = json.loads(manifest_path.read_text())
    assert [item["symbol"] for item in manifest["funds"]] == evidence["scopeSymbols"]
    assert manifest["requiredCoverage"] == {
        "startDate": evidence["scopeDateFrom"], "endDate": evidence["scopeDateTo"],
    }
    assert manifest["limitations"][:2] == [
        "仅适用于 research-assumption 研究假设模式", "不授予 strict-publication 或严格历史发布时间资格",
    ]
    assert os.stat(manifest_path.parent).st_mode & 0o077 == 0
    assert all(os.stat(path).st_mode & 0o077 == 0 for path in manifest_path.parent.iterdir())
    assert "responseRaw" not in json.dumps(manifest)


def test_explicitly_wrong_fund_type_rejects_before_nav_fetch_or_evidence_write(tmp_path):
    calls, identity_reader, nav_reader, qdii_reader = _readers()
    with pytest.raises(NavResearchAdmissionError, match="does not match current source identity"):
        prepare_nav_research_evidence(
            funds=[{"symbol": "110011.OF", "fundType": "domestic"}],
            research_mode="research-assumption", start=START, end=END,
            warmup_periods=2, tail_trading_days=2,
            research_decision="用户选择普通基金 T+1 研究假设",
            evidence_dir=tmp_path, recorded_by="n4-operator", valid_hours=24,
            identity_reader=identity_reader, nav_reader=nav_reader, qdii_reader=qdii_reader,
        )
    assert calls["identity"] == ["110011"]
    assert calls["nav"] == []
    assert list(tmp_path.iterdir()) == []


def test_missing_raw_evidence_or_insufficient_range_never_produces_admission(tmp_path):
    calls, identity_reader, _nav_reader, qdii_reader = _readers()
    store = StoreStub()
    with pytest.raises(NavResearchAdmissionError, match="raw response evidence is missing or invalid"):
        prepare_nav_research_evidence(
            funds=[FUNDS[0]], research_mode="research-assumption", start=START, end=END,
            warmup_periods=2, tail_trading_days=2,
            research_decision="用户确认国内 T+1 研究假设",
            evidence_dir=tmp_path, recorded_by="n4-operator", valid_hours=24,
            identity_reader=identity_reader, nav_reader=lambda _code: None,
            qdii_reader=qdii_reader,
        )
    assert store.record_calls == []
    assert calls["nav"] == []

    with pytest.raises(NavResearchAdmissionError, match="warmup, execution and tail"):
        _prepare(
            tmp_path,
            funds=[FUNDS[0]],
            start="2026-09-22",
            end="2026-09-23",
        )
    assert store.record_calls == []


def test_single_store_write_binds_full_batch_and_bounded_ttl(tmp_path):
    evidence, _calls = _prepare(tmp_path)
    store = StoreStub()

    admission = record_nav_research_admission(
        store, evidence, recorded_by="n4-operator", valid_hours=24,
    )

    assert len(store.record_calls) == 1
    recorded = store.record_calls[0]
    assert recorded["scope_symbols"] == [fund["symbol"] for fund in FUNDS]
    assert recorded["scope_date_from"] == evidence["scopeDateFrom"]
    assert recorded["scope_date_to"] == evidence["scopeDateTo"]
    assert recorded["adapter_revision"] == "efinance-fund-nav-raw-v1"
    assert recorded["source_revision"] == "eastmoney-fund-nav-raw-v1"
    assert recorded["credential_revision"] == "not-required"
    assert recorded["evidence_sha256"] == evidence["evidenceSha256"]
    assert admission["validUntil"]


@pytest.mark.parametrize("valid_hours", [0, 25, True])
def test_invalid_ttl_does_not_read_or_write_admission(tmp_path, valid_hours):
    evidence, _calls = _prepare(tmp_path)
    store = StoreStub()

    with pytest.raises(NavResearchAdmissionError, match="valid_hours"):
        record_nav_research_admission(
            store, evidence, recorded_by="n4-operator", valid_hours=valid_hours,
        )
    assert store.record_calls == []


def test_missing_or_tampered_evidence_never_registers(tmp_path):
    evidence, _calls = _prepare(tmp_path)
    manifest = Path(evidence["manifestPath"])
    manifest.unlink()
    store = StoreStub()

    with pytest.raises(NavResearchAdmissionError, match="manifest is missing"):
        record_nav_research_admission(
            store, evidence, recorded_by="n4-operator", valid_hours=1,
        )
    assert store.record_calls == []


def test_batch_will_not_drop_funds_from_an_existing_admission(tmp_path):
    evidence, _calls = _prepare(tmp_path, funds=[FUNDS[0]])
    store = StoreStub(existing={"scopeSymbols": ["110011.OF"]})

    with pytest.raises(NavResearchAdmissionError, match="fund outside this verified batch"):
        record_nav_research_admission(
            store, evidence, recorded_by="n4-operator", valid_hours=1,
        )
    assert store.record_calls == []


def test_cli_defaults_to_evidence_only_without_opening_or_writing_store(monkeypatch, capsys, tmp_path):
    from scripts import admit_nav_research as cli

    summary = {
        "funds": FUNDS,
        "researchMode": "research-assumption",
        "scopeDateFrom": "2026-09-07",
        "scopeDateTo": "2026-09-16",
        "manifestPath": str(tmp_path / "manifest.json"),
        "evidenceSha256": "a" * 64,
    }
    monkeypatch.setattr(cli, "prepare_nav_research_evidence", lambda **_kwargs: summary)

    def unexpected_store(*_args, **_kwargs):
        pytest.fail("evidence-only CLI must not inspect or open the DSA Store")

    monkeypatch.setattr(cli, "ThesisLedgerControlStore", unexpected_store)
    monkeypatch.setattr(cli, "_existing_store_path", unexpected_store)
    status = cli.main([
        "--fund", "161725.OF:domestic",
        "--fund", "110011.OF:qdii",
        "--fund", "118001.OF:qdii",
        "--research-mode", "research-assumption",
        "--research-decision", "用户明确选择研究假设",
        "--start", START,
        "--end", END,
        "--warmup-periods", "2",
        "--tail-trading-days", "2",
        "--evidence-dir", str(tmp_path),
        "--recorded-by", "n4-operator",
        "--valid-hours", "24",
    ])

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "evidence-only"
    assert payload["validUntil"] is None


def test_apply_store_preflight_requires_existing_current_dsa_database(monkeypatch, tmp_path):
    from scripts import admit_nav_research as cli

    missing = tmp_path / "missing.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(missing))
    with pytest.raises(NavResearchAdmissionError, match="existing DSA database"):
        cli._existing_store_path()
    assert not missing.exists()

    database = tmp_path / "dsa.sqlite"
    connection = sqlite3.connect(database)
    try:
        for table in (
            "thesis_ledger_policy_state",
            "thesis_ledger_route_admission_v3",
            "thesis_ledger_provider_config",
            "thesis_ledger_provider_health",
            "thesis_ledger_provider_tombstone",
        ):
            connection.execute(f"CREATE TABLE {table} (id INTEGER)")
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setenv("DATABASE_PATH", str(database))

    assert cli._existing_store_path() == database.resolve()


@pytest.mark.parametrize("provider_change", [
    {"enabled": False},
    {"configured": False},
    {"requiresCredential": True},
    {"tombstone": {"providerId": "efinance"}},
])
def test_apply_requires_current_efinance_provider_state(tmp_path, provider_change):
    evidence, _calls = _prepare(tmp_path, funds=[FUNDS[0]])
    provider = {
        "providerId": "efinance",
        "configured": True,
        "enabled": True,
        "requiresCredential": False,
        "tombstone": None,
        **provider_change,
    }
    store = StoreStub(provider=provider)

    with pytest.raises(NavResearchAdmissionError, match="current efinance provider state"):
        record_nav_research_admission(
            store, evidence, recorded_by="n4-operator", valid_hours=1,
        )
    assert store.record_calls == []
