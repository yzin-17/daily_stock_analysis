"""为内置历史资料提供只读展示元信息，不改变读取与准入判断。"""

from datetime import date
import json
from pathlib import Path
from urllib.parse import urlparse


def _documents(pack):
    documents = pack.get("sourceDocuments")
    if not isinstance(documents, list):
        return None
    result = []
    for document in documents:
        if not isinstance(document, dict) or any(
            not isinstance(document.get(key), str) or not document[key].strip()
            for key in ("id", "title", "url")
        ):
            return None
        url = urlparse(document["url"])
        if url.scheme not in ("http", "https") or not url.netloc:
            return None
        item = {key: document[key] for key in ("id", "title", "url")}
        published = document.get("publishedDate")
        if published is not None:
            if not isinstance(published, str) or date.fromisoformat(published).isoformat() != published:
                return None
            item["publishedDate"] = published
        result.append(item)
    return result


def attach_local_history(manifest, source_paths: dict[str, Path]):
    manifest["sourceKind"] = "local_history"
    for source in manifest["upstreamSources"]:
        path = source_paths.get(source["sourceId"])
        if path is None:
            continue
        try:
            raw = path.read_bytes()
            if len(raw) > 256 * 1024:
                continue
            pack = json.loads(raw)
            coverage = pack["coverage"]
            start, end = coverage["start"], coverage["end"]
            if date.fromisoformat(start).isoformat() != start or date.fromisoformat(end).isoformat() != end:
                continue
            if start > end:
                continue
            dataset = pack["datasetId"]
            if not isinstance(dataset, str) or not dataset.strip():
                continue
            symbol = pack.get("symbol")
            scope = pack.get("scope")
            if symbol is not None and (not isinstance(symbol, str) or not symbol.strip()):
                continue
            if symbol is None and not isinstance(scope, dict):
                continue
            metadata = {
                "datasetId": dataset,
                "symbols": [symbol] if symbol else [],
                "coverage": {"start": start, "end": end},
                "updateMode": "bundled",
            }
            if scope is not None:
                keys = ("market", "exchange", "assetType", "tradingMethod")
                if not isinstance(scope, dict) or any(
                    not isinstance(scope.get(key), str) or not scope[key].strip() for key in keys
                ):
                    continue
                metadata["scope"] = {key: scope[key] for key in keys}
            documents = _documents(pack)
            if documents is not None:
                metadata["documents"] = documents
            source["localHistory"] = metadata
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # 资料损坏时仍保留本地分类，客户端明确显示范围缺失。
            continue
    return manifest
