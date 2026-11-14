"""正文采集、快照原子保存、历史兼容与只读差异的确定性验证。"""

from hashlib import sha256
import io
import json
import sqlite3
from uuid import uuid4
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.services.rule_monitor_fetch import document_digest
from src.services.rule_monitor_observations import RuleMonitorObservations
from src.services.rule_monitor_registry import sources
from src.services.rule_monitor_snapshot import pdf_snapshot, text_snapshot
from src.services.rule_monitor_store import RuleMonitorStore
from src.services.rule_monitor_versions import RuleMonitorVersions, compare_snapshots

SOURCE = "official-rules/cn-stock-auction-fees"
URL = "https://www.sse.com.cn/rule"
TEXT = "受控收费规则的真实语义测试段落。" * 8


def record(store, digest, snapshot=None, **kwargs):
    job = str(uuid4())
    store.enqueue(job, [SOURCE]); store.claim("owner")
    assert RuleMonitorObservations(store).record(job, "owner", kind="document", identity=URL,
        name="受控资料", digest=digest, raw_digest=digest, snapshot=snapshot, **kwargs)
    assert store.finish(job, "owner")


def test_html_snapshot_preserves_paragraphs_references_and_existing_hash():
    raw = f'<nav>噪声</nav><main><p>{TEXT}</p><p>收费比例为0.1%。</p><a href="fees.pdf">附件</a></main>'.encode()
    result = document_digest(raw, "text/html")
    canonical = "html:" + TEXT + ' 收费比例为0.1%。 附件\n引用:["fees.pdf"]'
    assert result["contentHash"] == sha256(canonical.encode()).hexdigest()
    assert result["snapshot"]["text"] == TEXT + "\n收费比例为0.1%。\n附件\n\n引用链接：\nfees.pdf"
    assert "噪声" not in result["snapshot"]["text"]
    assert all(source.snapshot["format"] == "json" and json.loads(source.snapshot["text"])
        for source in sources().values())


def test_docx_snapshot_and_hash_preserve_original_itertext():
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="urn:word"><w:body>'
            f'<w:p><w:r><w:t>{TEXT}</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>第二段收费</w:t></w:r></w:p></w:body></w:document>')
    result = document_digest(body.getvalue(), "application/octet-stream")
    assert result["snapshot"]["text"] == TEXT + "\n第二段收费"
    assert result["contentHash"] == sha256(("docx:" + TEXT + " 第二段收费").encode()).hexdigest()
    assert text_snapshot("html", "😀" * 125001)["reason"] == "text_too_large"


def pdf_bytes(*, text=False, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(300, 200)
    if text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 10 100 Td (Fee rate is 0.1 percent.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("secret")
    result = io.BytesIO(); writer.write(result)
    return result.getvalue()


def test_pdf_process_extracts_text_and_preserves_byte_hash_and_unavailable_causes():
    body = pdf_bytes(text=True)
    result = document_digest(body, "application/pdf")
    assert "Fee rate is 0.1 percent." in result["snapshot"]["text"]
    assert result["contentHash"] == result["rawHash"] == sha256(body).hexdigest()
    assert pdf_snapshot(pdf_bytes())["reason"] == "text_unavailable"
    assert pdf_snapshot(pdf_bytes(encrypted=True))["reason"] == "pdf_encrypted"
    assert pdf_snapshot(b"%PDF-" + b"x" * 100)["reason"] == "extraction_failed"
    writer = PdfWriter()
    for _ in range(101):
        writer.add_blank_page(300, 200)
    oversized = io.BytesIO(); writer.write(oversized)
    assert pdf_snapshot(oversized.getvalue())["reason"] == "text_too_large"


def test_pdf_timeout_returns_stable_reason(monkeypatch):
    import subprocess
    from src.services import rule_monitor_snapshot
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 6)
    monkeypatch.setattr(rule_monitor_snapshot.subprocess, "run", timeout)
    assert pdf_snapshot(b"%PDF-")["reason"] == "extraction_timeout"


def test_snapshot_upgrade_reobserve_time_immutability_failure_and_reopen(tmp_path):
    now = [1000.0]
    path = tmp_path / "monitor.sqlite"
    store = RuleMonitorStore(path, clock=lambda: now[0])
    record(store, "a" * 64)
    before = RuleMonitorObservations(store).versions("document", URL)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE version_snapshots")
        db.execute("PRAGMA user_version=3")
    store = RuleMonitorStore(path, clock=lambda: now[0])
    reader = RuleMonitorVersions(store)
    assert reader.read("document", URL, "a" * 64)["snapshot"]["reason"] == "legacy_missing"
    assert RuleMonitorObservations(store).versions("document", URL) == before
    now[0] += 60
    record(store, "a" * 64, text_snapshot("html", TEXT))
    saved = reader.read("document", URL, "a" * 64)["snapshot"]
    assert saved["capturedAt"] > before[0]["firstSeenAt"]
    now[0] += 60
    record(store, "a" * 64, text_snapshot("html", "不得覆盖"))
    record(store, None, text_snapshot("html", "不得写入失败结果"), error="document_unavailable")
    reopened = RuleMonitorVersions(RuleMonitorStore(path))
    assert reopened.read("document", URL, "a" * 64)["snapshot"] == saved
    assert len(RuleMonitorObservations(store).versions("document", URL)) == 1
    with pytest.raises(KeyError):
        reader.read("pack", URL, "a" * 64)


def test_expired_owner_cannot_write_snapshot(tmp_path):
    now = [1000.0]
    store = RuleMonitorStore(tmp_path / "monitor.sqlite", clock=lambda: now[0])
    job = str(uuid4()); store.enqueue(job, [SOURCE]); store.claim("owner")
    now[0] += 361
    assert not RuleMonitorObservations(store).record(job, "owner", kind="document", identity=URL,
        name="晚到", digest="a" * 64, snapshot=text_snapshot("html", TEXT))
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM version_snapshots").fetchone()[0] == 0


def test_diff_add_delete_references_unchanged_missing_and_budget():
    diff = compare_snapshots({"text": "第一段\n费率0.1%\n旧附件"}, {"text": "第一段\n费率0.2%\n新附件"})
    assert diff["added"] == diff["removed"] == 2
    assert [line["text"] for line in diff["lines"] if line["type"] == "removed"] == ["费率0.1%", "旧附件"]
    assert compare_snapshots({"text": "一致"}, {"text": "一致"})["lines"] == []
    assert compare_snapshots({"text": None}, {"text": "正文"})["reason"] == "snapshot_unavailable"
    assert compare_snapshots({"text": "a" * 200001}, {"text": "正文"})["reason"] == "diff_too_large"
    assert compare_snapshots({"text": "a\n" * 2100}, {"text": "正文"})["reason"] == "diff_too_large"


def test_control_detail_auth_identity_and_read_only_two_version_path(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger_rule_monitor import router
    from src.services.rule_monitor_runtime import RuleMonitorRuntime
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "snapshot-control")
    store = RuleMonitorStore(tmp_path / "monitor.sqlite")
    record(store, "a" * 64, text_snapshot("html", "收费0.1%\n旧附件"))
    record(store, "b" * 64, text_snapshot("html", "收费0.2%\n新附件"))
    app = FastAPI(); app.state.rule_monitor = RuleMonitorRuntime(store); app.include_router(router)
    params = {"kind": "document", "identity": URL, "contentHash": "b" * 64, "baseHash": "a" * 64}
    root = "/thesis-ledger/control/rule-monitor/version"
    headers = {"Authorization": "Bearer snapshot-control"}
    with TestClient(app) as client:
        assert client.get(root, params=params).status_code == 401
        read = client.get(root, params=params, headers=headers)
        assert read.status_code == 200
        assert read.json()["comparison"]["added"] == 2
        assert read.json()["snapshot"]["text"] == "收费0.2%\n新附件"
        assert client.get(root, params={**params, "identity": URL + "other"}, headers=headers).status_code == 404
        assert client.get(root, params={**params, "baseHash": "c" * 64}, headers=headers).status_code == 404
        assert client.get(root, params={**params, "baseHash": "b" * 64}, headers=headers).status_code == 422
        assert client.get(root, params={**params, "contentHash": "../bad"}, headers=headers).status_code == 422
    assert len(RuleMonitorObservations(store).versions("document", URL)) == 2


def test_real_fetch_runtime_store_reopen_and_compare_controlled_html(tmp_path):
    import httpx
    from src.services.rule_monitor_fetch import fetch_document
    from src.services.rule_monitor_registry import Source
    from src.services.rule_monitor_runtime import RuleMonitorRuntime
    current = ["0.1%"]
    calls = []
    def serve(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=f'<main><p>{TEXT}</p><p>费率{current[0]}</p></main>',
            headers={"Content-Type": "text/html"})
    async def fetch(url, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            return await fetch_document(url, client=client, **kwargs)
    source = Source(SOURCE, "CN", "受控来源", "snapshot-v1", "a" * 64,
        ({"id": "rule", "title": "受控正文", "url": URL},), text_snapshot("json", '{"fee": 1}'))
    path = tmp_path / "monitor.sqlite"
    monitor = RuleMonitorRuntime(RuleMonitorStore(path), registry=lambda: {SOURCE: source}, fetch=fetch)
    for rate in ["0.1%", "0.2%"]:
        current[0] = rate
        monitor.enqueue(str(uuid4()), [SOURCE])
        assert monitor.run_pending_once()
        assert monitor.store.job()["status"] == "succeeded"
    reopened = RuleMonitorStore(path)
    versions = RuleMonitorObservations(reopened).versions("document", URL)
    read = RuleMonitorVersions(reopened).read("document", URL, versions[0]["contentHash"], versions[1]["contentHash"])
    assert read["comparison"]["added"] == read["comparison"]["removed"] == 1
    assert read["snapshot"]["text"].endswith("费率0.2%")
    assert len(calls) == 2


def test_snapshot_write_error_rolls_back_observation_and_unavailable_can_become_ready(tmp_path):
    store = RuleMonitorStore(tmp_path / "monitor.sqlite")
    record(store, "a" * 64, {"format": "pdf", "text": None, "reason": "extraction_timeout"})
    record(store, "a" * 64, text_snapshot("pdf", TEXT))
    assert RuleMonitorVersions(store).read("document", URL, "a" * 64)["snapshot"]["status"] == "available"
    job = str(uuid4()); store.enqueue(job, [SOURCE]); store.claim("owner")
    with pytest.raises(KeyError):
        RuleMonitorObservations(store).record(job, "owner", kind="document", identity=URL,
            name="错误快照", digest="b" * 64, snapshot={"format": "html"})
    assert len(RuleMonitorObservations(store).versions("document", URL)) == 1
    assert RuleMonitorObservations(store).status()[0]["contentHash"] == "a" * 64
