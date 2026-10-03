"""实际 SQLite 凭据/内容寻址文件与 RQData 标准化的受控组合读取。"""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.rqdata_fund_dividend_reader import fetch_rqdata_fund_dividends
from data_provider.rqdata_fund_split_reader import fetch_rqdata_fund_splits
from src.services.provider_credential_revision import provider_credential_revision
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_rqdata_mapped_read import read_rqdata_mapped_fund_event
from tests.test_rqdata_fund_identity_evidence import NOW, admission, bundle, encode


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, _zone=None):
            return NOW

    monkeypatch.setattr("src.services.thesis_ledger_rqdata_mapped_read.datetime", FrozenDateTime)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-rqdata-encryption-key")
    database = tmp_path / "control.db"
    store = ThesisLedgerControlStore(str(database))
    store.save_provider_config("rqdata", {
        "requestId": "rqdata-mapping-test", "credentials": {
            "method": "username_password", "values": {"username": "synthetic-user", "password": " secret "},
        },
    })
    evidence_store = MappingEvidenceStore(tmp_path / "thesis-ledger-mapping-evidence")
    credential_reader = Mock(side_effect=lambda: store.provider_credential_snapshot("rqdata"))
    return database, store, evidence_store, credential_reader


def arguments(inputs, kind, value=None):
    database, store, evidence_store, credentials = inputs
    content = encode(value or bundle())
    evidence_store.put(content)
    current = admission(content, kind)
    master = ("synthetic-v1", b"synthetic-master-key")
    current["credentialRevision"] = provider_credential_revision(store.provider_credential_snapshot("rqdata"), *master)
    options = dict(
        database_path=str(database), start="2025-02-01", end="2025-03-01", data_as_of=NOW.isoformat(),
        read_admission=lambda: deepcopy(current), read_credentials=credentials,
        read_master_key=lambda: master, timeout_seconds=4,
    )
    return current, options


@pytest.mark.parametrize("kind", ["split", "dividend"])
def test_mapping_supplies_verified_arguments_and_actual_standardization_retains_full_evidence(inputs, monkeypatch, kind):
    current, options = arguments(inputs, kind)
    frame = pd.DataFrame({"split_ratio": [2]}, index=pd.to_datetime(["2025-02-20"]))
    if kind == "dividend":
        frame = pd.DataFrame({
            "dividend_before_tax": ["0.125"], "book_closure_date": ["2025-02-19"], "payable_date": ["2025-02-21"],
        }, index=pd.to_datetime(["2025-02-20"]))
    api = SimpleNamespace(fund=SimpleNamespace(get_split=Mock(return_value=frame), get_dividend=Mock(return_value=frame)))

    def isolated(factory, requested_kind, symbol, **kwargs):
        assert (factory.username, factory.password) == ("synthetic-user", " secret ")
        assert requested_kind == kind
        assert kwargs.pop("timeout_seconds") == 4
        assert kwargs["query_fund_code"] == "159516" and kwargs["instrument_type"] == "ETF"
        if kind == "dividend":
            assert kwargs["currency"] == "CNY"
        else:
            assert "currency" not in kwargs
        reader = fetch_rqdata_fund_splits if kind == "split" else fetch_rqdata_fund_dividends
        return reader(api, symbol, **kwargs, before_call=lambda: None, after_call=lambda: None)

    execution = Mock(side_effect=isolated)
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated", execution)
    result = read_rqdata_mapped_fund_event(kind, "159516.SZ", **options)
    assert result["facts"][0]["symbol"] == "159516.SZ"
    assert result["coverage"]["complete"] is False
    assert result["identityEvidence"] == {
        "ref": current["evidenceRef"], "sha256": current["evidenceSha256"], "content": encode(bundle()).decode(),
    }
    if kind == "dividend":
        assert result["facts"][0]["currency"] == "CNY"
    execution.assert_called_once()


def test_missing_current_admission_never_reads_credentials(inputs, monkeypatch):
    _current, options = arguments(inputs, "split")
    options["read_admission"] = lambda: None
    execution = Mock()
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated", execution)
    with pytest.raises(ValueError, match="rqdata_identity_not_admitted"):
        read_rqdata_mapped_fund_event("split", "159516.SZ", **options)
    inputs[3].assert_not_called()
    execution.assert_not_called()


@pytest.mark.parametrize("change", ["missing", "tampered", "wrong-symbol", "missing-currency"])
def test_invalid_identity_never_reads_account_or_calls_sdk(inputs, monkeypatch, change):
    value = bundle(currency=change != "missing-currency")
    current, options = arguments(inputs, "dividend", value)
    path = Path(options["database_path"]).parent / "thesis-ledger-mapping-evidence" / (current["evidenceSha256"] + ".json")
    if change == "missing":
        path.unlink()
    elif change == "tampered":
        path.write_bytes(b"invalid-identity")
    elif change == "wrong-symbol":
        current["scopeSymbols"] = ["159516.SH"]
    execution = Mock()
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated", execution)
    with pytest.raises((ValueError, FileNotFoundError)):
        read_rqdata_mapped_fund_event("dividend", "159516.SZ", **options)
    inputs[3].assert_not_called()
    execution.assert_not_called()


@pytest.mark.parametrize("change", ["admission-rotate", "revoke", "file-tamper", "account-rotate"])
def test_mutation_during_read_rejects_late_result(inputs, monkeypatch, change):
    current, options = arguments(inputs, "split")

    def isolated(*_args, **_kwargs):
        if change == "admission-rotate":
            current["recordVersion"] += 1
        elif change == "revoke":
            current["invalidatedAt"] = NOW.isoformat()
        elif change == "file-tamper":
            path = Path(options["database_path"]).parent / "thesis-ledger-mapping-evidence" / (current["evidenceSha256"] + ".json")
            path.write_bytes(b"altered-after-read")
        elif change == "account-rotate":
            inputs[1].save_provider_config("rqdata", {
                "requestId": "rotate", "credentials": {"method": "username_password", "values": {"password": "rotated"}},
            })
        return {"facts": [], "coverage": {"complete": False}}

    execution = Mock(side_effect=isolated)
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated", execution)
    with pytest.raises(ValueError):
        read_rqdata_mapped_fund_event("split", "159516.SZ", **options)
    execution.assert_called_once()
