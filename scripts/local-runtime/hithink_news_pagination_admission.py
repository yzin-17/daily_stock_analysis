"""将既有招商银行新闻准入迁移到分页修订，原范围与有效期逐字段保护。"""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

from news_admission_extension import canonical_bytes, check_previous_evidence, evidence_path, read_record, record_sha256

KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "NEWS_SEARCH"}
TARGET = {"providerId": "hithink", "upstreamSource": "hithink-news-stock-timeline"}
OLD = ("hithink-stock-news-v1", "hithink-stock-timeline-first-page-v1")
NEW = ("hithink-stock-news-offset-v2", "hithink-stock-timeline-offset-v2")
CHANGED = {"adapter_revision", "source_revision", "evidence_ref", "evidence_sha256", "recorded_by", "recorded_at", "record_version"}


def validate_previous(row, at, credential):
    assert row["status"] == "admitted" and row["invalidated_at"] is None and row["invalidation_reason"] is None
    assert row["consumer"] == "thesis-ledger" and json.loads(row["route_key_json"]) == KEY
    assert (row["provider_id"], row["upstream_source"]) == (TARGET["providerId"], TARGET["upstreamSource"])
    assert json.loads(row["scope_symbols_json"]) == ["600036.SH"], "不能更换准入标的"
    assert datetime.fromisoformat(row["valid_from"]) <= at < datetime.fromisoformat(row["valid_until"]), "原准入不在有效期内"
    assert (row["adapter_revision"], row["source_revision"]) == OLD, "不是支持的旧分页修订"
    assert row["credential_revision"] == credential, "凭据修订变化"


def verify_unchanged(previous, current):
    assert set(previous) == set(current)
    assert all(previous[k] == current[k] for k in previous.keys() - CHANGED), "受保护准入字段变化"
    assert (current["adapter_revision"], current["source_revision"]) == NEW
    assert current["record_version"] == previous["record_version"] + 1


def capture(settings):
    from data_provider.hithink_news import fetch_hithink_news
    class Observer:
        attempts = 0
        def send(self, event):
            if event[0] == "attempt": self.attempts += 1
            if event[0] == "http_status": assert event[1] == 200
    observer = Observer()
    deadline = time.monotonic() + 12
    first = fetch_hithink_news(settings, "600036.SH", observer, deadline, max_results=5)
    assert first.has_more and first.offset and first.offset != "first", "首屏未提供后续位置"
    second = fetch_hithink_news(settings, "600036.SH", observer, deadline, max_results=5, offset=first.offset)
    assert not second.has_more or second.offset not in {"first", first.offset}, "后续页位置未前进"
    assert first.records and second.records and {r.source_item_id for r in first.records} != {r.source_item_id for r in second.records}
    assert observer.attempts == 2
    return {"upstreamAttempts": 2, "pages": [
        {"responseSha256": page.response_sha256, "count": len(page.records), "hasMore": page.has_more}
        for page in (first, second)
    ]}


def verify(db, current):
    directory = db.parent / "thesis-ledger-route-evidence"
    check_previous_evidence(directory, current)
    evidence = json.loads(evidence_path(directory, current["evidence_sha256"]).read_bytes())
    previous = evidence["previousAdmission"]
    check_previous_evidence(directory, previous)
    assert record_sha256(previous) == evidence["previousRecordSha256"]
    verify_unchanged(previous, current)
    assert evidence["operation"] == "news-offset-v2" and evidence["proof"]["upstreamAttempts"] == 2
    return evidence


def main(args):
    from arena_qualification import load_news_source
    from data_provider.thesis_ledger_sources.hithink_news import ADAPTER_REVISION, SOURCE_REVISION
    from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
    assert (ADAPTER_REVISION, SOURCE_REVISION) == NEW, "先通过正式更新入口同步分页代码"
    db, store, snapshot, settings = load_news_source()
    with store._connect() as c: row = read_record(c, KEY, TARGET)
    credential = _provider_credential_revision_from_snapshot(snapshot)
    expected_policy = store.effective_policy_v3()

    def guard():
        assert store.provider_credential_snapshot("hithink") == snapshot, "凭据或配置变化"
        policy = store.effective_policy_v3()
        assert policy and policy["revision"] == expected_policy["revision"] and policy["enabled"] and any(
            r["key"] == KEY and r.get("enabled", True) and any(t["providerId"] == "hithink" and t["upstreamSource"] == TARGET["upstreamSource"]
                                    and t["routeIndex"] == 0 for t in r["targets"]) for r in policy["routes"]), "路由变化"
    guard()
    already = (row["adapter_revision"], row["source_revision"]) == NEW
    if already:
        saved = verify(db, row)
        if args["mode"] == "apply":
            assert (args["version"], args["digest"]) in {
                (row["record_version"], record_sha256(row)),
                (saved["previousAdmission"]["record_version"], saved["previousRecordSha256"]),
            }, "计划变化"
    else:
        validate_previous(row, datetime.now(timezone.utc), credential)
        check_previous_evidence(db.parent / "thesis-ledger-route-evidence", row)
    if args["mode"] == "plan":
        return {"version": row["record_version"], "digest": record_sha256(row),
                "operation": "reuse" if already else "qualify-offset-v2", "preserveScopeAndValidity": True}
    if args["mode"] == "apply" and not already:
        assert (args["version"], args["digest"]) == (row["record_version"], record_sha256(row)), "计划变化"
        proof = capture(settings)
        at = datetime.now(timezone.utc)
        evidence = {"operation": "news-offset-v2", "observedAt": at.isoformat(), "proof": proof,
                    "previousAdmission": row, "previousRecordSha256": record_sha256(row)}
        raw = canonical_bytes(evidence)
        from hashlib import sha256
        digest = sha256(raw).hexdigest()
        with closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as c, c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            assert read_record(c, KEY, TARGET) == row, "准入记录变化"
            validate_previous(row, at, credential)
            guard()
            path = evidence_path(db.parent / "thesis-ledger-route-evidence", digest)
            with path.open("xb") as out: out.write(raw)
            path.chmod(0o600)
            updated = c.execute(
                "UPDATE thesis_ledger_route_admission_v3 SET adapter_revision=?,source_revision=?,evidence_ref=?,evidence_sha256=?,"
                "recorded_by=?,recorded_at=?,record_version=record_version+1 "
                "WHERE consumer=? AND route_key_json=? AND provider_id=? AND upstream_source=? AND record_version=?",
                (*NEW, "sha256:" + digest, digest, "hithink-news-pagination", at.isoformat(), row["consumer"],
                 row["route_key_json"], row["provider_id"], row["upstream_source"], row["record_version"]))
            assert updated.rowcount == 1
            current = read_record(c, KEY, TARGET)
            verify_unchanged(row, current)
            verify(db, current)
            row = current
    else:
        assert already, "分页版本尚未核验"
        verify(db, row)
    assert store.get_route_admission_v3(key=KEY, target=TARGET)["admissionState"] == "admitted"
    return {"recordVersion": row["record_version"], "evidenceSha256": row["evidence_sha256"],
            "scopeSymbols": json.loads(row["scope_symbols_json"]), "scopeDateFrom": row["scope_date_from"],
            "scopeDateTo": row["scope_date_to"], "validUntil": row["valid_until"],
            "writes": args["mode"] == "apply" and not already}


if __name__ == "__main__":
    print(json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
