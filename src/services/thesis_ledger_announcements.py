"""中国内地公告记录；查询与事实读取均不触发网络获取。"""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile


def announcement_root():
    return Path(os.getenv("DATABASE_PATH", "./data/stock_analysis.db")).resolve().parent / "thesis-ledger-announcements"


class AnnouncementRepository:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else announcement_root()

    def save(self, record):
        identity = record["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
            raise ValueError("公告标识无效")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{identity}.json"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, sort_keys=True)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def records(self):
        records = []
        for path in sorted(self.root.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("id") != path.stem or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value.get("symbol", "")):
                raise ValueError("公告库存记录无效")
            records.append(value)
        return records

    def original(self, record):
        digest = record["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("公告原件摘要无效")
        path = self.root / "originals" / f"{digest}.pdf"
        if not path.exists():
            path = self.root / f"{record['id']}.pdf"
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("公告原件摘要不匹配")
        return raw

    def save_original(self, raw):
        digest = sha256(raw).hexdigest()
        directory = self.root / "originals"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        try:
            temporary.replace(directory / f"{digest}.pdf")
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    def query(self, query="", page=1, page_size=50):
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("公告分页参数无效")
        by_url = {item["url"]: item for item in seed_announcements()}
        for item in self.records():
            old = by_url.get(item["url"])
            if old:
                item = {**item, "uses": sorted(set(item["uses"] + old["uses"]))}
            by_url[item["url"]] = item
        keyword = query.strip().casefold()
        items = [item for item in by_url.values() if keyword in f"{item['title']} {item['symbol']}".casefold()]
        items.sort(key=lambda item: (item["publishedDate"], item["id"]), reverse=True)
        start = (page - 1) * page_size
        fields = ("id", "symbol", "title", "url", "publishedDate", "fetchedAt", "uses", "downloadState", "parseState", "detail")
        return {"providerId": "official-states", "total": len(items), "page": page, "pageSize": page_size,
                "items": [{key: item[key] for key in fields if key in item} for item in items[start:start + page_size]]}


def seed_announcements():
    from src.services.thesis_ledger_trading_status import DATA_PATH, RISK_DATA_PATH
    grouped = {}
    for path, use in ((DATA_PATH, "suspension"), (RISK_DATA_PATH, "risk")):
        if not path.is_file():
            continue
        pack = json.loads(path.read_text(encoding="utf-8"))
        for doc in pack["sourceDocuments"]:
            item = grouped.setdefault(doc["url"], {
                "id": doc["id"], "symbol": pack["symbol"], "title": doc["title"], "url": doc["url"],
                "publishedDate": doc["publishedDate"], "uses": [], "downloadState": "pending", "parseState": "parsed",
                "detail": "已收录核实的状态资料；原件可通过原文链接查看",
            })
            item["uses"].append(use)
    return list(grouped.values())


def observation_clock():
    return datetime.now(timezone.utc).isoformat()
