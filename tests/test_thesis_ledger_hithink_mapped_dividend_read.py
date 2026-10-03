"""合成准入和 HTTP 结果检验 HiThink 分红读取的身份与快照边界。"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data_provider.hithink_fund_dividend_reader import fetch_hithink_fund_dividends
from src.services.provider_credential_revision import provider_credential_revision
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_ADAPTER_REVISION, HITHINK_DIVIDEND_SOURCE_REVISION,
)
from src.services.thesis_ledger_hithink_mapped_dividend_read import read_hithink_mapped_fund_dividends
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from tests.test_hithink_fund_identity_evidence import SYMBOL, evidence


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def millis(day):
    return int(datetime.fromisoformat(day + "T00:00:00+08:00").timestamp() * 1000)


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, _zone=None):
            return NOW

    monkeypatch.setattr("src.services.thesis_ledger_hithink_mapped_dividend_read.datetime", FrozenDateTime)
    content, current = evidence()
    current.update(adapterRevision=HITHINK_DIVIDEND_ADAPTER_REVISION,
                   sourceRevision=HITHINK_DIVIDEND_SOURCE_REVISION)
    MappingEvidenceStore(tmp_path / "thesis-ledger-mapping-evidence").put(content)
    state = SimpleNamespace(key="synthetic-key", version="v1", master=b"synthetic-master")

    def credentials():
        return ProviderCredentialSnapshot.create(
            "hithink", "environment", "api_key", {"apiKey": state.key}, 1, 1,
        )

    def master():
        return state.version, state.master

    current["credentialRevision"] = provider_credential_revision(credentials(), *master())
    response = {
        "symbol": SYMBOL, "fundType": "exchange", "responseSha256": sha256(b"synthetic-response").hexdigest(),
        "observedAt": "2026-09-29T00:00:00Z", "historyComplete": False,
        "items": [{"progress": "实施", "publish_date_ms": millis("2025-06-01"),
                   "registration_date_ms": millis("2025-06-17"),
                   "ex_dividend_date_ms": millis("2025-06-18"),
                   "payment_date_ms": millis("2025-06-27"),
                   "per_ten_cash_before_tax": "0.880"}],
    }
    fetch = Mock(side_effect=lambda *args, **kwargs: deepcopy(response))
    read_credentials = Mock(side_effect=credentials)
    options = dict(database_path=str(tmp_path / "control.sqlite"), start="2025-06-01", end="2025-06-30",
                   data_as_of="2026-09-29T00:00:00Z", read_admission=lambda: deepcopy(current),
                   read_credentials=read_credentials, read_master_key=master, fetch=fetch, timeout_seconds=5)
    return SimpleNamespace(state=state, current=current, response=response, fetch=fetch,
                           read_credentials=read_credentials, options=options)


def read(inputs, **changes):
    return read_hithink_mapped_fund_dividends(SYMBOL, **{**inputs.options, **changes})


def test_current_evidence_and_one_frozen_key_produce_incomplete_cash_fact(inputs):
    mapped = read(inputs)
    fact = mapped.result["facts"][0]
    assert (fact["symbol"], fact["currency"], fact["cashAmount"]) == (SYMBOL, "CNY", "0.088")
    assert fact["providerRevision"] == mapped.result["providerRevision"]
    assert mapped.result["coverage"] == {"start": "2025-06-01", "end": "2025-06-30", "complete": False}
    assert mapped.identity.content and mapped.identity.evidence_ref == inputs.current["evidenceRef"]
    assert inputs.fetch.call_args.args == (SYMBOL,)
    assert inputs.fetch.call_args.kwargs["fund_type"] == "exchange"
    assert inputs.fetch.call_args.kwargs["api_key"] == "synthetic-key"
    assert inputs.read_credentials.call_count == 2


def test_real_bounded_reader_receives_frozen_key_and_explicit_fund_type(inputs):
    class Response:
        status_code = 200

        def iter_content(self, chunk_size):
            assert chunk_size == 65536
            yield json.dumps({"code": 0, "data": {"item": inputs.response["items"],
                                                  "dividend_count": 1}}).encode()

        def close(self):
            pass

    http_get = Mock(return_value=Response())

    def fetch(symbol, *, fund_type, api_key, timeout_seconds):
        return fetch_hithink_fund_dividends(
            symbol, fund_type=fund_type, api_key=api_key, timeout_seconds=timeout_seconds,
            http_get=http_get, clock=lambda: NOW,
        )

    result = read(inputs, fetch=fetch)
    request = http_get.call_args
    assert request.kwargs["params"] == {"fund_type": "exchange", "thscode": SYMBOL}
    assert request.kwargs["headers"]["X-api-key"] == "synthetic-key"
    assert request.kwargs["allow_redirects"] is False
    assert result.result["facts"][0]["cashAmount"] == "0.088"
    assert result.result["coverage"]["complete"] is False


def test_missing_identity_fails_before_reading_credential_or_source(inputs):
    inputs.current["evidenceRef"] = "sha256:" + "f" * 64
    with pytest.raises(Exception):
        read(inputs)
    inputs.read_credentials.assert_not_called()
    inputs.fetch.assert_not_called()


@pytest.mark.parametrize("mutation", ["revoke", "key", "master", "evidence"])
def test_change_during_source_read_rejects_late_result(inputs, mutation):
    def fetch(*_args, **_kwargs):
        if mutation == "revoke":
            inputs.current["admissionState"] = "revoked"
        elif mutation == "key":
            inputs.state.key = "rotated-key"
        elif mutation == "master":
            inputs.state.master = b"rotated-master"
        else:
            inputs.current["evidenceSha256"] = "f" * 64
        return deepcopy(inputs.response)

    with pytest.raises(ValueError):
        read(inputs, fetch=fetch)


@pytest.mark.parametrize("change", [
    {"progress": "2"}, {"per_ten_cash_before_tax": None},
])
def test_unknown_progress_or_missing_cash_never_becomes_empty_success(inputs, change):
    inputs.response["items"][0].update(change)
    with pytest.raises(ValueError, match="hithink_mapped_invalid_result"):
        read(inputs)


def test_observation_later_than_freeze_is_rejected(inputs):
    with pytest.raises(ValueError, match="hithink_mapped_invalid_result"):
        read(inputs, data_as_of="2026-09-28T23:59:59Z")
