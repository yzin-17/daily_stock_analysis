"""收费资料拥有的有限监测目录；公开接口不接收路径或外部URL。"""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2] / "data_provider"
PACKS = {
    "official-rules/cn-stock-auction-fees": ("CN", "上交所股票收费主源", "public_fees/cn-stock-auction-2023-08.json"),
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

    def public(self):
        return {"identity": self.identity, "market": self.market, "name": self.name,
            "datasetId": self.dataset, "packHash": self.digest, "documentCount": len(self.documents)}


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
        if any(not allowed_document_url(document["url"]) for document in documents):
            raise ValueError("资料包包含未登记的公开文档地址")
        references = [{key: document[key] for key in ("id", "title", "url")} for document in documents]
        current = CURRENT_REFERENCES[market]
        if all(document["url"] != current["url"] for document in references):
            references.append(current)
        from src.services.rule_monitor_snapshot import text_snapshot
        snapshot = text_snapshot("json", json.dumps(pack, ensure_ascii=False, sort_keys=True, indent=2))
        result[identity] = Source(identity, market, name, pack["datasetId"], sha256(raw).hexdigest(), tuple(references), snapshot)
    from src.services.us_live_rules_refresh import registered_source
    current_us = registered_source()
    result[current_us.identity] = current_us
    return result
