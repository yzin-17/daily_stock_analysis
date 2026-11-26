"""收费资料拥有的有限监测目录；公开接口不接收路径或外部URL。"""

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2] / "data_provider"
CN_STOCK_FEE_SOURCE = "official-rules/cn-stock-auction-fees"
CN_STOCK_FEE_REVIEW = "public_fees/cn-stock-auction-2023-08.review.json"
PACKS = {
    CN_STOCK_FEE_SOURCE: ("CN", "上交所股票收费主源", "public_fees/cn-stock-auction-2023-08.json"),
    "official-rules/cn-stock-auction-fees-archive": ("CN", "上交所股票收费备用", "public_fees/cn-stock-auction-archive-2023-08.json"),
    "official-hk-rules/hkex-trading-fee": ("HK", "港股交易费", "market_reference/hk-00147-fees.json"),
    "official-hk-rules/hk-stock-public-fees": ("HK", "港股扩展公共收费", "market_reference/hk-00147-public-fees.json"),
    "official-us-rules/us-section31-exchange-t2": ("US", "美股Section31", "market_reference/us-gme-fees.json"),
    "official-us-rules/us-stock-public-fees": ("US", "美股扩展公共收费", "market_reference/us-gme-public-fees.json"),
    "official-rules/cn-etf-handling-fee": ("CN", "上交所ETF经手费", "market_reference/cn-510300-fees.json"),
    "official-hk-rules/hk-etf-public-fees": ("HK", "港股ETF公共收费", "market_reference/hk-02800-public-fees.json"),
    "official-us-rules/us-etf-public-fees": ("US", "美股ETF公共收费", "market_reference/us-spy-public-fees.json"),
}
HOSTS = frozenset({"www.hengqin.gov.cn", "shanghai.chinatax.gov.cn", "english.www.gov.cn",
    "www.ndrc.gov.cn", "jrj.wuhan.gov.cn", "shanxi.chinatax.gov.cn", "www.sse.com.cn",
    "jrj.sh.gov.cn", "www.hkex.com.hk", "www.finra.org", "www.govinfo.gov", "www.sec.gov"})
CURRENT_REFERENCES = {
    "CN": {"id": "current-fee-guide", "title": "上交所收费规则现行状态及附件",
        "url": "https://www.sse.com.cn/lawandrules/sselawsrules2025/charge/c/c_20250610_10781461.shtml"},
    "HK": {"id": "current-transaction-fees", "title": "港交所现行交易费用与通告引用",
        "url": "https://www.hkex.com.hk/Services/Rules-and-Forms-and-Fees/Fees/Securities-(Hong-Kong)/Trading/Transaction?sc_lang=en"},
    "US": {"id": "current-regulatory-fees", "title": "FINRA现行监管费用规则与引用",
        "url": "https://www.finra.org/rules-guidance/rulebooks/corporate-organization/section-1-member-regulatory-fees"},
}
# 已核验的官方正文或同修订摘要仅用于监测；资料包原始证据与摘要保持原值。
DOCUMENT_READ_URLS = {
    "https://www.govinfo.gov/content/pkg/FR-2004-07-07/pdf/04-15081.pdf":
        "https://www.govinfo.gov/content/pkg/FR-2004-07-07/html/04-15081.htm",
    "https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf":
        "https://www.govinfo.gov/content/pkg/FR-2020-10-20/html/2020-23141.htm",
    "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2022/086_22_SEHK_OP_Trading-Fee_e.pdf":
        "https://www.hkex.com.hk/Services/Rules-and-Forms-and-Fees/Rules/SEHK/Rules-of-the-Exchange/Rules/Rule-Update_Operation-Procedures-for-Trading-Fee?sc_lang=en",
    "https://www.sec.gov/files/rules/sro/finra/2026/34-106409.pdf":
        "https://www.govinfo.gov/content/pkg/FR-2026-09-23/html/2026-19392.htm",
    "https://www.hkex.com.hk/-/media/hkex-market/services/circulars-and-notices/participant-and-members-circulars/sehk/2014/ct05314b":
        "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2014/CT05314B.pdf",
}
DOCUMENT_READ_TITLES = {
    "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2022/086_22_SEHK_OP_Trading-Fee_e.pdf":
        "港交所交易费修订正文摘要及附件（2022-11-14）",
}


def allowed_document_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in HOSTS and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and not parsed.fragment)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Source:
    identity: str
    market: str
    name: str
    dataset: str
    digest: str
    documents: tuple
    snapshot: dict | None = None
    refresh_baseline: dict[str, str] | None = None

    def public(self):
        return {"identity": self.identity, "market": self.market, "name": self.name,
            "datasetId": self.dataset, "packHash": self.digest, "documentCount": len(self.documents)}


def _read_references(documents):
    if any(not allowed_document_url(document["url"]) for document in documents):
        raise ValueError("资料包包含未登记的公开文档地址")
    references = tuple({"id": document["id"],
        "title": DOCUMENT_READ_TITLES.get(document["url"], document["title"]),
        "url": DOCUMENT_READ_URLS.get(document["url"], document["url"])} for document in documents)
    if any(not allowed_document_url(document["url"]) for document in references):
        raise ValueError("监测正文包含未登记的公开文档地址")
    return references


def _cn_stock_fee_review(digest, references):
    try:
        review = json.loads((ROOT / CN_STOCK_FEE_REVIEW).read_bytes())
        if review["packSha256"] != digest:
            return references, None
        attached = [*references, *_read_references(review["attachments"])]
        baseline = review["contentHashes"]
        if set(baseline) == {doc["url"] for doc in attached}:
            return attached, baseline
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return references, None


def sources():
    result = {}
    for identity, (market, name, filename) in PACKS.items():
        raw = (ROOT / filename).read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError("资料包超过监测目录上限")
        pack = json.loads(raw)
        documents = pack["sourceDocuments"]
        if not isinstance(documents, list) or not documents or len(documents) > 24:
            raise ValueError("资料包文档范围无效")
        references = list(_read_references(documents))
        current = CURRENT_REFERENCES[market]
        if all(document["url"] != current["url"] for document in references):
            references.append(current)
        digest = sha256(raw).hexdigest()
        baseline = None
        if identity == CN_STOCK_FEE_SOURCE:
            references, baseline = _cn_stock_fee_review(digest, references)
        from src.services.rule_monitor_snapshot import text_snapshot
        snapshot = text_snapshot("json", json.dumps(pack, ensure_ascii=False, sort_keys=True, indent=2))
        result[identity] = Source(identity, market, name, pack["datasetId"], digest, tuple(references), snapshot, baseline)
    from src.services.us_live_rules_refresh import registered_source
    current_us = registered_source()
    result[current_us.identity] = replace(current_us, documents=_read_references(current_us.documents))
    return result
