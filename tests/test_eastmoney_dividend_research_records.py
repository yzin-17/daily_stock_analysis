"""普通研究逐行质量、有限事件核对及配置复用的离线边界。"""

from datetime import datetime, timezone
from hashlib import sha256
import pytest
from data_provider.eastmoney_dividend_research_records import normalize_eastmoney_research_records
from src.services.thesis_ledger_eastmoney_known_cash import read_eastmoney_known_cash
from src.services.thesis_ledger_known_cash_state_v3 import KnownCashReadError
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot


def request():
    from copy import deepcopy
    from tests.test_thesis_ledger_hithink_known_cash import EXCHANGE
    value = deepcopy(EXCHANGE["request"])
    value.update(symbol="510300.SH", start="2025-06-01", end="2026-02-01",
                 dataAsOf=datetime.now(timezone.utc).isoformat())
    value["routeTarget"].update(providerId="akshare", upstreamSource="eastmoney")
    value["researchPolicy"]["uncertainRecords"] = "exclude"
    return value


def page(rows=None, code="510300"):
    if rows is None:
        rows = [["2025年", "2025-06-17", "2025-06-18", "每10份派现金0.8800元", "2025-06-27"]]
    body = f"<title>基金({code})基金分红送配</title><table><tr>"
    body += "".join(f"<th>{name}</th>" for name in ("年份", "权益登记日", "除息日", "每10份分红", "分红发放日")) + "</tr>"
    body += "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows) + "</table>"
    return {"symbol": "510300.SH", "rawBusinessResponse": body,
            "responseSha256": sha256(body.encode()).hexdigest(), "observedAt": "2026-02-02T00:00:00Z"}


def snapshot():
    return ProviderCredentialSnapshot.create("akshare", "control", None, {}, 3, 0)


def test_missing_status_and_announcement_never_fabricated_but_reconciled_cash_retained():
    result = read_eastmoney_known_cash(request(), snapshot(), fetch=lambda _: page())
    row = result["records"][0]
    assert row["sourceStatus"] is None and row["announcementDate"] is None
    assert row["normalizedStatus"] == "unknown" and row["statusBasis"] == "event-reconciled"
    assert row["amountPerFundUnit"] == "0.0880"
    assert row["sourceAmount"] == {"value": "0.8800", "unit": "per-ten-fund-units"}
    assert result["assessment"]["observations"][0]["identityBasis"] == "supplier-code-echo"
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("kind", ["missing", "amount", "order", "cancel-text", "duplicate", "conflict", "outside", "empty"])
def test_bad_rows_do_not_destroy_valid_response_or_silently_disappear(kind):
    good = ["2025年", "2025-06-17", "2025-06-18", "每10份派现金0.88元", "2025-06-27"]
    bad = good.copy()
    if kind == "missing": bad[4] = "--"
    elif kind == "amount": bad[3] = "0.88"
    elif kind == "order": bad[1] = "2025-06-18"
    elif kind == "cancel-text": bad[3] = "取消派息"
    elif kind == "conflict": bad[3] = "每10份派现金0.89元"
    elif kind == "outside": bad[2] = "2024-06-18"
    elif kind == "empty": bad = ["暂无分红信息!"]
    result = normalize_eastmoney_research_records(page([good, bad]), request())
    counts = result["assessment"]["counts"]
    assert sum(counts.values()) == 2
    expected = {"duplicate": "duplicate", "outside": "outside-window", "empty": "placeholder"}.get(kind, "excluded")
    assert counts[expected] > 0
    if kind != "duplicate": assert result["assessment"]["observations"][1]["issues"]
    assert len(result["records"]) == (0 if kind in {"missing", "amount", "order", "cancel-text", "conflict"} else 1)


def test_unknown_valid_event_and_decimal_precision_preserved_without_reconciliation():
    rows = [["2025年", "2025-06-17", "2025-06-18", "每10份派现金1.123456789012345678901234567890元", "2025-06-27"]]
    result = normalize_eastmoney_research_records(page(rows), request())
    assert result["records"][0]["amountPerFundUnit"] == "0.112345678901234567890123456789"
    assert result["records"][0]["statusBasis"] == "unverified"


@pytest.mark.parametrize("kind", ["identity", "hash", "clock", "unit"])
def test_invalid_envelope_separate_from_upstream_failure(kind):
    raw = page()
    if kind == "identity": raw = page(code="510050")
    elif kind == "hash": raw["responseSha256"] = "0" * 64
    elif kind == "clock": raw["observedAt"] = "2099-01-01T00:00:00Z"
    else:
        raw["rawBusinessResponse"] = raw["rawBusinessResponse"].replace("<th>每10份分红</th>", "<th>分红</th>")
        raw["responseSha256"] = sha256(raw["rawBusinessResponse"].encode()).hexdigest()
    with pytest.raises(KnownCashReadError) as caught:
        read_eastmoney_known_cash(request(), snapshot(), fetch=lambda _: raw)
    assert caught.value.code == "invalid_response"


def test_source_failure_keeps_upstream_error():
    def fail(_): raise TimeoutError()
    with pytest.raises(KnownCashReadError) as caught:
        read_eastmoney_known_cash(request(), snapshot(), fetch=fail)
    assert caught.value.code == "upstream_failure"


def test_control_registry_supports_credentialless_eastmoney_without_window_admission(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from src.services.thesis_ledger_known_cash_v3 import execute_known_cash_request
    import src.services.thesis_ledger_known_cash_state_v3 as controls
    monkeypatch.setattr(controls, "provider_dependency_ready", lambda *_: True)
    req = request()
    target = req["routeTarget"]
    policy = {"contractVersion": 3, "revision": req["effectivePolicyRevision"],
              "sourceDesiredRevision": req["desiredRevision"], "enabled": True,
              "routes": [{"key": req["routeKey"], "targets": [{**target, "eligible": False, "reason": "not_admitted"}]}]}
    catalog = {"integrity": "complete", "catalogRevision": req["catalogRevision"],
               "entries": [{"key": req["routeKey"], "target": {k: target[k] for k in ("providerId", "upstreamSource")}, "state": "not_admitted"}]}
    provider = {"providerId": "akshare", "enabled": True, "configured": True,
                "requiresCredential": False, "configVersion": 3, "tombstone": None}
    store = SimpleNamespace(effective_policy_v3=lambda: deepcopy(policy),
                            provider_registry=lambda: [deepcopy(provider)], provider_credential_snapshot=lambda _: snapshot())
    runtime = SimpleNamespace(store=store, market_route_catalog_v3=lambda: deepcopy(catalog))
    calls = []
    def read(r, s):
        calls.append(r)
        return read_eastmoney_known_cash(r, s, fetch=lambda _: page())
    value = execute_known_cash_request(runtime, req, reader=read)
    assert value["source"]["controlIdentity"]["credentialRevision"] == "not-required" and len(calls) == 1
    provider["enabled"] = False
    with pytest.raises(KnownCashReadError): execute_known_cash_request(runtime, req, reader=read)
    assert len(calls) == 1
