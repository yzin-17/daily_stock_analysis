"""从当前准入绑定的本地证据解析拆分日期，保留来源观测时间。"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from data_provider.eastmoney_fund_split_reader import fetch_fund_split_observations
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_split_mapping_v3 import resolve_split_mapping_observations


def read_mapped_split_events(
    database_path: str, request: dict[str, Any], admission: dict[str, Any],
    *, reader: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not database_path or database_path == ":memory:":
        raise ValueError("拆分映射需要持久化 Control 存储")
    store = MappingEvidenceStore(Path(database_path).expanduser().resolve().parent / "thesis-ledger-mapping-evidence")
    evidence = store.read(admission["evidenceRef"])
    options = {"evidence_bytes": evidence, "admission": admission, "observed_at": datetime.now(timezone.utc)}
    resolve_split_mapping_observations([], **options)
    mappings = [item for item in json.loads(evidence)["mappings"]
                if item["symbol"] == request["symbol"] and request["start"] <= item["effectiveDate"] <= request["end"]]
    if not mappings:
        raise ValueError("请求窗口缺少拆分映射证据")
    # 查询按源日期，消费按交易除权日期；窗口边界不能漏掉前日登记事件。
    start = min([request["start"], *[item["sourceConversionDate"] for item in mappings]])
    result = (reader or fetch_fund_split_observations)(request["symbol"], start=start, end=request["end"])
    observations = result.get("observations")
    if not isinstance(observations, list):
        raise ValueError("拆分来源没有观测记录")
    mapped = resolve_split_mapping_observations(observations, **options)
    revision = result.get("providerRevision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("拆分来源缺少修订")
    revision = "mapped-split:" + sha256((revision + admission["evidenceSha256"]).encode()).hexdigest()
    facts = [{
        "symbol": item["symbol"], "market": "CN", "instrumentType": "ETF", "type": "SPLIT",
        "effectiveDate": item["effectiveDate"],
        **({"recordDate": item["recordDate"]} if item["recordDate"] is not None else {}),
        "ratio": item["sourceRatioPerUnit"], "occurredAt": f'{item["effectiveDate"]}T00:00:00+08:00',
        "availableAt": item["observedAt"], "provider": "akshare", "providerRevision": revision,
    } for item in mapped if request["start"] <= item["effectiveDate"] <= request["end"]]
    return {"facts": facts, "providerRevision": revision, "dateMappingEvidence": {
        "ref": admission["evidenceRef"], "sha256": admission["evidenceSha256"], "content": evidence.decode("utf-8"),
    }}
