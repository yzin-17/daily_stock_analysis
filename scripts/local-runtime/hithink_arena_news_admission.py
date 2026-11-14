"""为招商银行正式新闻读取消费登记有限期的同花顺准入，保留旧记录及证据链。"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import sys
from tempfile import NamedTemporaryFile
import time

from news_admission_extension import (
    canonical_bytes, check_previous_evidence, evidence_path, read_record, record_sha256,
)

SYMBOL = "600036.SH"
KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "NEWS_SEARCH"}
TARGET = {"providerId": "hithink", "upstreamSource": "hithink-news-stock-timeline"}
OPERATOR = "hithink-arena-news"
TERM = timedelta(days=7)


def previous_record(connection):
    connection.row_factory = sqlite3.Row
    return read_record(connection, KEY, TARGET)


def check_scope(row, at):
    assert row["status"] == "admitted" and row["invalidated_at"] is None and row["invalidation_reason"] is None
    symbols = json.loads(row["scope_symbols_json"])
    if symbols == ["600519.SH"]:
        assert datetime.fromisoformat(row["valid_until"]) <= at, "原标的仍有有效准入"
    else:
        assert symbols == [SYMBOL] and row["recorded_by"] == OPERATOR, "不能替换其他标的的准入"


def write_evidence(directory, evidence):
    digest = record_sha256(evidence)
    path = evidence_path(directory, digest)
    raw = canonical_bytes(evidence)
    if not path.exists():
        with NamedTemporaryFile(dir=directory) as pending:
            pending.write(raw)
            pending.flush()
            os.fsync(pending.fileno())
            os.link(pending.name, path)
    assert path.read_bytes() == raw
    return digest


def apply_record(db, previous, evidence, guard):
    directory = db.parent / "thesis-ledger-route-evidence"
    at = datetime.fromisoformat(evidence["observedAt"])
    with closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = previous_record(c)
        assert row == previous, "新闻准入已变化，请重新查看计划"
        check_scope(row, at)
        check_previous_evidence(directory, row)
        guard()
        digest = write_evidence(directory, evidence)
        updated = c.execute(
            "UPDATE thesis_ledger_route_admission_v3 SET scope_symbols_json=?, scope_date_from=?, "
            "scope_date_to=?, valid_from=?, valid_until=?, evidence_ref=?, evidence_sha256=?, "
            "recorded_by=?, recorded_at=?, record_version=record_version+1 "
            "WHERE consumer=? AND route_key_json=? AND provider_id=? AND upstream_source=? AND record_version=?",
            (json.dumps([SYMBOL]), evidence["scopeDateFrom"], evidence["scopeDateTo"], at.isoformat(),
             evidence["validUntil"], "sha256:" + digest, digest, OPERATOR, at.isoformat(),
             row["consumer"], row["route_key_json"], row["provider_id"], row["upstream_source"], row["record_version"]))
        assert updated.rowcount == 1
        current = previous_record(c)
        verify_record(directory, current)
    return current


def verify_record(directory, current):
    check_previous_evidence(directory, current)
    evidence = json.loads(evidence_path(directory, current["evidence_sha256"]).read_bytes())
    previous = evidence["previousAdmission"]
    check_previous_evidence(directory, previous)
    assert record_sha256(previous) == evidence["previousRecordSha256"]
    at = datetime.fromisoformat(evidence["observedAt"])
    check_scope(previous, at)
    expected = {**previous, "scope_symbols_json": json.dumps([SYMBOL]),
                "scope_date_from": (at - timedelta(days=3)).date().isoformat(),
                "scope_date_to": (at + TERM).date().isoformat(), "valid_from": at.isoformat(),
                "valid_until": (at + TERM).isoformat(), "record_version": previous["record_version"] + 1,
                "evidence_ref": "sha256:" + current["evidence_sha256"], "evidence_sha256": current["evidence_sha256"],
                "recorded_by": OPERATOR, "recorded_at": at.isoformat()}
    assert current == expected and evidence["symbol"] == SYMBOL and evidence["target"] == TARGET
    assert evidence["validUntil"] == expected["valid_until"]
    assert (evidence["scopeDateFrom"], evidence["scopeDateTo"]) == (current["scope_date_from"], current["scope_date_to"])
    return evidence


def capture(settings, at):
    from data_provider.hithink_news import fetch_hithink_news
    class Probe:
        attempts = 0
        status = None

        def send(self, event):
            if event[0] == "attempt":
                self.attempts += 1
            elif event[0] == "http_status":
                self.status = event[1]

    probe = Probe()
    page = fetch_hithink_news(settings, SYMBOL, probe, time.monotonic() + 6, max_results=10)
    assert probe.attempts == 1 and probe.status == 200 and 0 < len(page.records) <= 10
    items = [{"id": r.source_item_id, "title": r.title, "url": r.url, "publishedAt": r.published_at,
              "publisher": r.publisher} for r in page.records]
    assert any(r["publishedAt"] and at - timedelta(days=3) <= datetime.fromisoformat(r["publishedAt"]) <= at
               for r in items), "首屏没有近期带发布时间的条目"
    return {"items": items, "responseSha256": page.response_sha256, "truncated": page.has_more}


def main(args):
    from arena_qualification import load_news_source
    from data_provider.thesis_ledger_sources.hithink_news import ADAPTER_REVISION, SOURCE_REVISION
    from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
    db, store, snapshot, settings = load_news_source()
    directory = db.parent / "thesis-ledger-route-evidence"
    with store._connect() as c:
        row = previous_record(c)
    cred = _provider_credential_revision_from_snapshot(snapshot)
    assert cred and (row["adapter_revision"], row["source_revision"], row["credential_revision"]) == (
        ADAPTER_REVISION, SOURCE_REVISION, cred), "接入版本或凭据变化"

    def guard():
        policy = store.effective_policy_v3()
        assert policy and policy["enabled"] and any(
            r["key"] == KEY and any(t["providerId"] == TARGET["providerId"]
                                    and t["upstreamSource"] == TARGET["upstreamSource"] and t["routeIndex"] == 0
                                    for t in r["targets"]) for r in policy["routes"]), "新闻路由已变化"
        assert store.provider_credential_snapshot("hithink") == snapshot
        assert any(p["providerId"] == "hithink" and p["enabled"] for p in store.provider_registry())

    guard()
    now = datetime.now(timezone.utc)
    mode = args["mode"]
    assert mode in {"plan", "apply", "verify"}
    check_scope(row, now)
    check_previous_evidence(directory, row)
    already = row["recorded_by"] == OPERATOR
    saved = verify_record(directory, row) if already else None
    if already:
        assert datetime.fromisoformat(row["valid_from"]) <= now, "既有准入的生效时间晚于当前时刻"
    reusable = already and datetime.fromisoformat(row["valid_until"]) > now + timedelta(days=1)
    if mode == "plan":
        return {"symbol": SYMBOL, "previousScope": json.loads(row["scope_symbols_json"]),
                "version": row["record_version"], "digest": record_sha256(row),
                "operation": "reuse" if reusable else "qualify", "termHours": 168}
    if mode == "verify":
        assert already and store.get_route_admission_v3(key=KEY, target=TARGET)["admissionState"] == "admitted"
    elif reusable:
        expected = (args["version"], args["digest"])
        assert expected in {(row["record_version"], record_sha256(row)),
                            (saved["previousAdmission"]["record_version"], saved["previousRecordSha256"])}, "计划版本已变化"
        assert store.get_route_admission_v3(key=KEY, target=TARGET)["admissionState"] == "admitted"
    else:
        assert row["record_version"] == args["version"] and record_sha256(row) == args["digest"], "计划版本已变化"
        page = capture(settings, now)
        at = datetime.now(timezone.utc)
        evidence = {**page, "target": TARGET, "symbol": SYMBOL, "license": "account-news-metadata",
                    "observedAt": at.isoformat(), "validUntil": (at + TERM).isoformat(),
                    "scopeDateFrom": (at - timedelta(days=3)).date().isoformat(),
                    "scopeDateTo": (at + TERM).date().isoformat(),
                    "previousAdmission": row, "previousRecordSha256": record_sha256(row)}
        row = apply_record(db, row, evidence, guard)
        assert store.get_route_admission_v3(key=KEY, target=TARGET)["admissionState"] == "admitted"
    return {"symbol": SYMBOL, "recordVersion": row["record_version"], "evidenceSha256": row["evidence_sha256"],
            "scopeDateFrom": row["scope_date_from"], "scopeDateTo": row["scope_date_to"],
            "validUntil": row["valid_until"], "upstreamAttempts": int(mode == "apply" and not reusable)}


if __name__ == "__main__":
    print(json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
