"""实际临时 Store、HMAC 和精确 HTTP Reader 的离线组合，不证明真实准入。"""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.provider_credential_revision import provider_credential_revision
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _secret_key
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_tushare_mapped_read import (
    TUSHARE_FUND_DIV_ADAPTER_REVISION, TUSHARE_FUND_DIV_SOURCE_REVISION, read_tushare_mapped_fund_dividends,
)
from tests.test_tushare_fund_identity_evidence import NOW, admission, bundle, encode


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    config = SimpleNamespace(tushare_token="synthetic-token")
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-mapped-master")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "synthetic-v1")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic.example.test/api")
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, _zone=None):
            return NOW

    monkeypatch.setattr("src.services.thesis_ledger_tushare_mapped_read.datetime", FrozenDateTime)
    monkeypatch.setattr("data_provider.tushare_fund_dividend_reader.datetime", FrozenDateTime)
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr("src.services.thesis_ledger_tushare_mapped_read.time",
                        SimpleNamespace(monotonic=lambda: clock.value))
    database = str(tmp_path / "control.sqlite")
    store = ThesisLedgerControlStore(database)
    runtime = ThesisLedgerProviderRuntime(store)
    value = bundle()
    content = encode(value)
    evidence = MappingEvidenceStore(tmp_path / "thesis-ledger-mapping-evidence")
    evidence.put(content)
    current = admission(content)
    current.update(adapterRevision=TUSHARE_FUND_DIV_ADAPTER_REVISION, sourceRevision=TUSHARE_FUND_DIV_SOURCE_REVISION)
    current["credentialRevision"] = provider_credential_revision(store.provider_credential_snapshot("tushare"), *_secret_key())
    credentials = Mock(side_effect=lambda: store.provider_credential_snapshot("tushare"))
    master = Mock(side_effect=_secret_key)
    builder = Mock(side_effect=lambda snapshot: runtime._adapter("tushare", snapshot=snapshot))
    source = Mock(return_value=Mock(status_code=200, text=json.dumps({
        "code": 0, "data": {
            "fields": ["ts_code", "ann_date", "imp_anndate", "div_proc", "record_date", "ex_date", "pay_date", "div_cash"],
            "items": [["159516.SZ", "20250201", "20250202", "实施", "20250219", "20250220", "20250221", "0.015"]],
        },
    })))
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", source)
    options = dict(database_path=database, start="2025-02-01", end="2025-03-01", data_as_of=NOW.isoformat(),
                   read_admission=Mock(side_effect=lambda: deepcopy(current)), read_credentials=credentials,
                   read_master_key=master, build_fetcher=builder, timeout_seconds=5)
    return SimpleNamespace(config=config, clock=clock, store=store, runtime=runtime, content=content,
                           current=current, evidence=evidence, source=source, options=options)


def read(inputs, **changes):
    return read_tushare_mapped_fund_dividends("159516.SZ", **{**inputs.options, **changes})


def evidence_path(inputs):
    return Path(inputs.options["database_path"]).parent / "thesis-ledger-mapping-evidence" / (
        inputs.current["evidenceSha256"] + ".json"
    )


def test_real_store_snapshot_hmac_and_exact_http_reader_preserve_internal_evidence(inputs):
    before = deepcopy(inputs.current)
    result = read(inputs)
    identity = result.identity
    assert identity.content == inputs.content
    assert identity.evidence_ref == before["evidenceRef"]
    assert identity.evidence_sha256 == before["evidenceSha256"]
    assert identity.query_fund_code == "159516.SZ" and identity.currency == "CNY"
    assert inputs.current == before
    fact = result.result["facts"][0]
    assert (fact["symbol"], fact["currency"], fact["cashAmount"]) == ("159516.SZ", "CNY", "0.015")
    assert fact["availableAt"] == NOW.isoformat().replace("+00:00", "Z")
    assert result.result["observations"][0]["observedAt"] == fact["availableAt"]
    assert result.result["coverage"]["complete"] is False
    assert "tushareIdentityEvidence" not in result.result and "identityEvidence" not in result.result
    assert "credentialRevision" not in result.result
    frozen = inputs.options["build_fetcher"].call_args.args[0]
    with pytest.raises(TypeError):
        frozen.values["token"] = "changed"
    assert inputs.source.call_args.args == (frozen.values["httpUrl"],)
    request = inputs.source.call_args.kwargs
    assert request["json"]["token"] == frozen.values["token"]
    assert request["json"]["api_name"] == "fund_div"
    assert request["json"]["params"] == {"ts_code": identity.query_fund_code}
    assert request["allow_redirects"] is False and 0 < request["timeout"] <= 5
    inputs.source.assert_called_once()
    assert inputs.options["read_credentials"].call_count == 2


@pytest.mark.parametrize("currency", ["HKD", "USD"])
def test_verified_currency_drives_actual_standardization_without_a_market_default(inputs, currency):
    value = bundle()
    value["mappings"][0]["dividendCurrencyEvidence"]["currency"] = currency
    content = encode(value)
    inputs.evidence.put(content)
    replacement = admission(content)
    for field in ("evidenceRef", "evidenceSha256"):
        inputs.current[field] = replacement[field]
    result = read(inputs)
    assert result.identity.currency == result.result["facts"][0]["currency"] == currency


@pytest.mark.parametrize("change", ["missing", "revoked", "adapter", "source", "hmac-format", "wrong-target"])
def test_missing_or_invalid_admission_rejects_before_credentials_or_source(inputs, change):
    if change == "missing":
        inputs.options["read_admission"] = Mock(return_value=None)
    elif change == "revoked":
        inputs.current["invalidatedAt"] = NOW.isoformat()
    elif change in {"adapter", "source"}:
        inputs.current[change + "Revision"] = "changed-local-contract"
    elif change == "hmac-format":
        inputs.current["credentialRevision"] = "unsafe-revision"
    else:
        inputs.current["target"] = {"providerId": "rqdata", "upstreamSource": "rqdata"}
    with pytest.raises(ValueError):
        read(inputs)
    inputs.options["read_credentials"].assert_not_called()
    inputs.options["read_master_key"].assert_not_called()
    inputs.options["build_fetcher"].assert_not_called()
    inputs.source.assert_not_called()


@pytest.mark.parametrize("change", ["missing-file", "tampered-file", "symbol", "currency", "future-nanosecond"])
def test_invalid_identity_never_reads_credentials_or_constructs_adapter(inputs, change):
    if change == "missing-file":
        evidence_path(inputs).unlink()
    elif change == "tampered-file":
        evidence_path(inputs).write_bytes(b"tampered-identity")
    else:
        value = bundle()
        if change == "symbol":
            value["mappings"][0]["queryFundCode"] = "159516.OF"
        elif change == "currency":
            del value["mappings"][0]["dividendCurrencyEvidence"]
        else:
            value["mappings"][0]["observedAt"] = "2026-09-27T11:00:00.000000001Z"
        content = encode(value)
        inputs.evidence.put(content)
        replacement = admission(content)
        inputs.current.update(evidenceRef=replacement["evidenceRef"], evidenceSha256=replacement["evidenceSha256"])
    with pytest.raises((ValueError, FileNotFoundError)):
        read(inputs)
    inputs.options["read_credentials"].assert_not_called()
    inputs.options["build_fetcher"].assert_not_called()
    inputs.source.assert_not_called()


@pytest.mark.parametrize("change", ["hmac", "token", "endpoint", "master", "master-version", "control-source"])
def test_actual_security_revision_mismatch_rejects_before_adapter_or_http(inputs, monkeypatch, change):
    if change == "hmac":
        inputs.current["credentialRevision"] = "hmac-sha256-v1:" + "0" * 64
    elif change == "token":
        inputs.config.tushare_token = "rotated-token"
    elif change == "endpoint":
        monkeypatch.setenv("TUSHARE_HTTP_URL", "https://rotated.example.test/api")
    elif change == "master":
        monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "rotated-master")
    elif change == "master-version":
        monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "rotated-v2")
    else:
        frozen = inputs.store.provider_credential_snapshot("tushare")
        inputs.options["read_credentials"] = Mock(return_value=ProviderCredentialSnapshot.create(
            "tushare", "control", "token", frozen.values, 1, 1,
        ))
    with pytest.raises(ValueError, match="tushare_mapped_credential_not_admitted"):
        read(inputs)
    inputs.options["build_fetcher"].assert_not_called()
    inputs.source.assert_not_called()


@pytest.mark.parametrize("change", [
    "revoked", "record", "adapter", "source", "file", "deleted-file", "token", "endpoint", "master", "master-version",
])
def test_during_read_mutations_are_rejected_after_exact_single_source_call(inputs, monkeypatch, change):
    original = inputs.source.return_value

    def response(*args, **kwargs):
        if change == "revoked":
            inputs.current["invalidatedAt"] = NOW.isoformat()
        elif change == "record":
            inputs.current["recordVersion"] += 1
        elif change in {"adapter", "source"}:
            inputs.current[change + "Revision"] = "changed-local-contract"
        elif change == "file":
            evidence_path(inputs).write_bytes(inputs.content + b" ")
        elif change == "deleted-file":
            evidence_path(inputs).unlink()
        elif change == "token":
            inputs.config.tushare_token = "rotated-token"
        elif change == "endpoint":
            monkeypatch.setenv("TUSHARE_HTTP_URL", "https://rotated.example.test/api")
        elif change == "master":
            monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "rotated-master")
        else:
            monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "rotated-v2")
        return original

    inputs.source.side_effect = response
    with pytest.raises((ValueError, FileNotFoundError)):
        read(inputs)
    inputs.source.assert_called_once()
    assert inputs.source.call_args.kwargs["json"]["api_name"] == "fund_div"


def test_empty_response_keeps_stable_content_revision_incomplete_coverage_and_shared_rate_counter(inputs):
    response = json.loads(inputs.source.return_value.text)
    response["data"]["items"] = []
    inputs.source.return_value.text = json.dumps(response)
    first, second = read(inputs), read(inputs)
    assert first.result["facts"] == second.result["facts"] == []
    assert first.result["providerRevision"] == second.result["providerRevision"]
    assert first.result["providerRevision"].startswith("tushare-fund-div-content-v1:")
    assert first.result["coverage"]["complete"] is second.result["coverage"]["complete"] is False
    assert inputs.runtime.adapters["tushare"]._call_count == 2


@pytest.mark.parametrize("failure", [PermissionError("synthetic denial"), TimeoutError("synthetic timeout")])
def test_source_failure_has_no_retry_sdk_or_endpoint_fallback(inputs, failure):
    inputs.source.side_effect = failure
    with pytest.raises((ValueError, TimeoutError), match="^tushare_mapped_"):
        read(inputs)
    inputs.source.assert_called_once()
    assert inputs.source.call_args.kwargs["json"]["api_name"] == "fund_div"


@pytest.mark.parametrize("budget", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_total_budget_rejects_before_admission_or_account(inputs, budget):
    with pytest.raises(ValueError, match="tushare_mapped_invalid_timeout"):
        read(inputs, timeout_seconds=budget)
    inputs.options["read_admission"].assert_not_called()
    inputs.options["read_credentials"].assert_not_called()
    inputs.source.assert_not_called()


def test_builder_uses_total_remaining_budget_and_late_http_result_is_rejected(inputs):
    original_builder = inputs.options["build_fetcher"].side_effect
    original_response = inputs.source.return_value

    def builder(snapshot):
        inputs.clock.value += 1
        return original_builder(snapshot)

    def late_response(*args, **kwargs):
        assert 0 < kwargs["timeout"] <= 4
        inputs.clock.value += 5
        return original_response

    inputs.options["build_fetcher"].side_effect = builder
    inputs.source.side_effect = late_response
    with pytest.raises(TimeoutError, match="tushare_mapped_timeout"):
        read(inputs)
    inputs.source.assert_called_once()


def test_builder_finishing_after_deadline_never_calls_source(inputs):
    original_builder = inputs.options["build_fetcher"].side_effect

    def late_builder(snapshot):
        result = original_builder(snapshot)
        inputs.clock.value += 6
        return result

    inputs.options["build_fetcher"].side_effect = late_builder
    with pytest.raises(TimeoutError, match="tushare_mapped_timeout"):
        read(inputs)
    inputs.source.assert_not_called()


def test_read_after_expiry_rechecks_original_nanosecond_boundary_without_mutating_admission(inputs, monkeypatch):
    class ReadDateTime(datetime):
        @classmethod
        def now(cls, _zone=None):
            return NOW if inputs.source.call_count == 0 else NOW + timedelta(microseconds=1)

    monkeypatch.setattr("src.services.thesis_ledger_tushare_mapped_read.datetime", ReadDateTime)
    inputs.current["validUntil"] = "2026-09-27T12:00:00.000000001Z"
    before = deepcopy(inputs.current)
    with pytest.raises(ValueError, match="tushare_identity_not_admitted"):
        read(inputs)
    inputs.source.assert_called_once()
    assert inputs.current == before


@pytest.mark.parametrize("change", ["token", "endpoint", "complete"])
def test_trusted_builder_must_return_the_pinned_existing_mixin_and_incomplete_result(inputs, change):
    original_builder = inputs.options["build_fetcher"].side_effect

    def builder(snapshot):
        fetcher = original_builder(snapshot)
        if change == "token":
            fetcher._token = "wrong-token"
        elif change == "endpoint":
            fetcher._http_url = "https://wrong.example.test/api"
        else:
            original_read = fetcher.get_fund_dividends_for_source

            def wrong_complete(*args, **kwargs):
                result = original_read(*args, **kwargs)
                result["coverage"]["complete"] = True
                return result

            fetcher.get_fund_dividends_for_source = wrong_complete
        return fetcher

    inputs.options["build_fetcher"].side_effect = builder
    with pytest.raises(ValueError, match="tushare_mapped_"):
        read(inputs)
    if change == "complete":
        inputs.source.assert_called_once()
    else:
        inputs.source.assert_not_called()
