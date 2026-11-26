"""官方正文与修订摘要映射、原资料保护及新地址独立基准；网络完全受控。"""

from hashlib import sha256
import json
from uuid import uuid4

import httpx
import pytest

import src.services.rule_monitor_registry as registry_module
from src.services.rule_monitor_fetch import DocumentError, fetch_document
from src.services.rule_monitor_history import RuleMonitorHistory
from tests.test_rule_monitor_runtime import setup
from tests.test_rule_monitor_snapshots import pdf_bytes
from src.services.us_live_rules_refresh import DOCUMENTS, IDENTITY, registered_source

PDF = "https://www.govinfo.gov/content/pkg/FR-2004-07-07/pdf/04-15081.pdf"
HTML = "https://www.govinfo.gov/content/pkg/FR-2004-07-07/html/04-15081.htm"
FINRA_PDF = "https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf"
FINRA_HTML = "https://www.govinfo.gov/content/pkg/FR-2020-10-20/html/2020-23141.htm"
HKEX_PDF = "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2022/086_22_SEHK_OP_Trading-Fee_e.pdf"
HKEX_HTML = "https://www.hkex.com.hk/Services/Rules-and-Forms-and-Fees/Rules/SEHK/Rules-of-the-Exchange/Rules/Rule-Update_Operation-Procedures-for-Trading-Fee?sc_lang=en"
SEC_PDF = "https://www.sec.gov/files/rules/sro/finra/2026/34-106409.pdf"
SEC_HTML = "https://www.govinfo.gov/content/pkg/FR-2026-09-23/html/2026-19392.htm"
SFC_ORIGINAL = "https://www.hkex.com.hk/-/media/hkex-market/services/circulars-and-notices/participant-and-members-circulars/sehk/2014/ct05314b"
SFC_PDF = "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2014/CT05314B.pdf"
EXPECTED_READ_URLS = {PDF: HTML, FINRA_PDF: FINRA_HTML, HKEX_PDF: HKEX_HTML, SEC_PDF: SEC_HTML, SFC_ORIGINAL: SFC_PDF}
HKEX_TITLE = "港交所交易费修订正文摘要及附件（2022-11-14）"
HK_SOURCE_IDS = (
    "official-hk-rules/hkex-trading-fee",
    "official-hk-rules/hk-stock-public-fees",
    "official-hk-rules/hk-etf-public-fees",
)
SOURCE_IDS = (
    "official-us-rules/us-section31-exchange-t2",
    "official-us-rules/us-stock-public-fees",
    "official-us-rules/us-etf-public-fees",
)


def test_official_text_mapping_preserves_pack_bytes_identity_and_other_documents():
    registry = registry_module.sources()
    for identity, (_, _, filename) in registry_module.PACKS.items():
        raw = (registry_module.ROOT / filename).read_bytes()
        pack = json.loads(raw)
        source = registry[identity]
        assert source.digest == sha256(raw).hexdigest()
        assert json.loads(source.snapshot["text"]) == pack
        assert source.dataset == pack["datasetId"]
        projected = {doc["id"]: doc for doc in source.documents}
        for document in pack["sourceDocuments"]:
            expected = EXPECTED_READ_URLS.get(document["url"], document["url"])
            title = HKEX_TITLE if document["url"] == HKEX_PDF else document["title"]
            assert projected[document["id"]] == {
                "id": document["id"], "title": title, "url": expected,
            }
        if identity in SOURCE_IDS:
            original = next(doc for doc in pack["sourceDocuments"] if doc["id"] == "sec-31-charge-date")
            assert original["url"] == PDF
            assert original["sha256"] == "9b922313ec9ee68295a89c8f8c989c0c92989fcecedbd756b5ab872f45d282d9"
            assert original["publishedDate"] == "2004-07-07"
            assert original["observedAt"] == "2026-10-07T08:12:39.174824+00:00"
        if identity in SOURCE_IDS[1:]:
            original = next(doc for doc in pack["sourceDocuments"] if doc["id"] == "finra-taf-2020-schedule")
            assert original["url"] == FINRA_PDF
            assert original["sha256"] == "ddac2be95898c8d7fd43c02e5e1d92fd953c2ccb0a5bf312bd5cc4342bd13cd0"
            assert original["publishedDate"] is None
            assert original["observedAt"] == "2026-10-07T14:14:13.582475+00:00"
        if identity in HK_SOURCE_IDS:
            original = next(doc for doc in pack["sourceDocuments"] if doc["id"] == "hkex-086-2022")
            assert original["url"] == HKEX_PDF and original["title"] == "港交所交易费调整通告 LSD/086/2022"
            assert original["sha256"] == "ebf57bb5b3041ec0ae7bc209b07a22517acce0d3904c0dee12c4a2fdf0efbd1d"
            assert original["publishedDate"] == "2022-11-14"
            assert original["observedAt"] == "2026-10-07T08:12:18.131611+00:00"
        if identity in HK_SOURCE_IDS[1:]:
            original = next(doc for doc in pack["sourceDocuments"] if doc["id"] == "hkex-sfc-053-2014")
            assert original["url"] == SFC_ORIGINAL
            assert original["sha256"] == "5635f20beba7574dae3a7847c7e2ecfe0767c669eaab90af8776c072feec6a06"
            assert original["publishedDate"] == "2014-10-20"
            assert original["observedAt"] == "2026-10-07T14:10:46.732896+00:00"
    assert {key for key, source in registry.items() if any(doc["url"] == HTML for doc in source.documents)} == set(SOURCE_IDS)
    assert not any(doc["url"] == PDF for source in registry.values() for doc in source.documents)
    assert {key for key, source in registry.items() if any(doc["url"] == FINRA_HTML for doc in source.documents)} == set(SOURCE_IDS[1:])
    assert not any(doc["url"] == FINRA_PDF for source in registry.values() for doc in source.documents)
    assert {key for key, source in registry.items() if any(doc["url"] == HKEX_HTML for doc in source.documents)} == set(HK_SOURCE_IDS)
    original = registered_source()
    projected = registry[IDENTITY]
    assert projected.digest == original.digest and projected.snapshot == original.snapshot
    assert projected.dataset == original.dataset
    assert projected.documents == tuple({**doc, "url": EXPECTED_READ_URLS.get(doc["url"], doc["url"])} for doc in original.documents)
    assert next(digest for _, url, digest in DOCUMENTS if url == SEC_PDF) == "b45080f51ebf9208784588bed02da224ae76f64675deec6cc1ad005ad9393aae"
    assert SEC_PDF in original.snapshot["text"] and SEC_HTML not in original.snapshot["text"]
    assert {key for key, source in registry.items() if any(doc["url"] == SEC_HTML for doc in source.documents)} == {IDENTITY}
    assert {key for key, source in registry.items() if any(doc["url"] == SFC_PDF for doc in source.documents)} == set(HK_SOURCE_IDS[1:])
    assert not any(doc["url"] in EXPECTED_READ_URLS for source in registry.values() for doc in source.documents)


@pytest.mark.parametrize("url", [
    "http://www.govinfo.gov/rule", "https://127.0.0.1/rule", "https://example.com/rule",
    HTML + "#section", "https://user@www.govinfo.gov/rule",
])
@pytest.mark.parametrize("pdf", list(EXPECTED_READ_URLS))
def test_mapped_document_still_requires_registered_safe_url(monkeypatch, url, pdf):
    monkeypatch.setattr(registry_module, "DOCUMENT_READ_URLS", {**EXPECTED_READ_URLS, pdf: url})
    with pytest.raises(ValueError, match="监测正文包含未登记"):
        registry_module.sources()


@pytest.mark.parametrize("original,replacement,source_ids,marker,kind", [
    (PDF, HTML, SOURCE_IDS, "04-15081；34-49928；2004-07-07", "html"),
    (FINRA_PDF, FINRA_HTML, SOURCE_IDS[1:], "SR-FINRA-2020-032；34-90176；2020-23141；2024；0.000166；8.30", "html"),
    (HKEX_PDF, HKEX_HTML, HK_SOURCE_IDS, "14 Nov 2022；0.00565%；1 January 2023；086_22_OP_Trading-Fee_e.pdf", "html"),
    (SEC_PDF, SEC_HTML, (IDENTITY,), "34-106409；SR-FINRA-2026-021；2026-19392；October 1, 2026；December 31, 2026", "html"),
    (SFC_ORIGINAL, SFC_PDF, HK_SOURCE_IDS[1:], "CT/053/14；20/10/2014；1 November 2014；0.003%；0.0027%", "pdf"),
])
def test_shared_official_read_is_deduplicated_and_preserves_original_failure(tmp_path, monkeypatch, original, replacement, source_ids, marker, kind):
    monitor, calls = setup(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(registry_module, "DOCUMENT_READ_URLS", {key: value for key, value in EXPECTED_READ_URLS.items() if key != original})
        previous = registry_module.sources()
    monitor.registry = lambda: {key: previous[key] for key in source_ids}

    async def read(url, **kwargs):
        calls.append(url)
        if url == original:
            raise DocumentError("document_unavailable")
        if url == replacement:
            body = f"<html><body><pre>受控同提案官方正文：{marker}。"
            body += "收费日期定义、清算机构报告与结算日期规则的受控摘录。" * 8
            body += "</pre></body></html>"
            content, content_type = body.encode(), "text/html"
            if kind == "pdf":
                content, content_type = pdf_bytes(text=True), "application/pdf"
            transport = httpx.MockTransport(lambda request: httpx.Response(
                200, content=content, headers={"Content-Type": content_type}))
            async with httpx.AsyncClient(transport=transport) as client:
                result = await fetch_document(url, client=client, **kwargs)
                assert result["kind"] == kind and result["snapshot"]["text"]
                assert result["snapshot"]["reason"] is None
                return result
        return {"contentHash": "b" * 64, "rawHash": "c" * 64}

    monitor.fetch = read
    old_id = str(uuid4())
    monitor.enqueue(old_id, list(source_ids)); assert monitor.run_pending_once()
    history = RuleMonitorHistory(monitor.store)
    failed = history.read(issues_only=True)["items"][0]
    assert failed["status"] == "partial" and failed["counts"]["unavailable"] == 1
    assert [item["identity"] for item in failed["failures"]] == [original]
    before_config = monitor.store.config()

    current = registry_module.sources()
    monitor.registry = lambda: {key: current[key] for key in source_ids}
    calls.clear()
    new_id = str(uuid4())
    monitor.enqueue(new_id, list(source_ids)); assert monitor.run_pending_once()
    run = monitor.store.job(new_id)
    expected = {doc["url"] for source in monitor.registry().values() for doc in source.documents}
    assert run["status"] == "succeeded" and run["counts"]["unavailable"] == 0
    assert run["counts"]["documents"] == len(expected) == len(calls)
    assert set(calls) == expected and calls.count(replacement) == 1 and original not in calls
    assert monitor.observations.versions("document", replacement)[0]["contentHash"]
    assert history.read(issues_only=True)["items"] == [failed]
    assert monitor.store.config() == before_config
    old = next(item for item in monitor.status()["observations"] if item["identity"] == original)
    assert old["errorCode"] == "document_unavailable" and old["lastSuccessAt"] is None
