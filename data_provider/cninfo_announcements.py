"""巨潮公开公告的有界获取；单件失败保留元信息和其他成功项。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo
import html
import re
import time

import requests

from src.services.thesis_ledger_announcements import AnnouncementRepository, observation_clock
from data_provider.cninfo_announcement_query import HEADERS, query_announcements



def announcement_uses(title):
    uses = []
    if "停牌" in title or "复牌" in title:
        uses.append("suspension")
    if "风险警示" in title:
        uses.append("risk")
    return uses


def acquire_announcements(symbols, start, end, *, max_pages=20, repository=None, session=None, parse=False, force=False, budget_seconds=180):
    symbols = list(dict.fromkeys(symbols))
    if not symbols or any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value) for value in symbols):
        raise ValueError("请提供规范中国内地证券代码")
    if date.fromisoformat(start) > date.fromisoformat(end) or not 1 <= max_pages <= 100:
        raise ValueError("获取日期或页数无效")
    repo = repository or AnnouncementRepository()
    client = session or requests.Session()
    old = {record["id"]: record for record in repo.records()}
    deadline = time.monotonic() + budget_seconds
    items, errors, truncated = query_announcements(client, symbols, start, end, max_pages, deadline,
                                                  root=repo.root, force=force, validate=_metadata)
    found = {}
    for symbol, item in items:
        try:
            record = _metadata(item, symbol)
            previous = found.get(record["id"]) or old.get(record["id"], {})
            record = {**previous, **record, "uses": sorted(set(record["uses"] + previous.get("uses", []))),
                      "downloadState": previous.get("downloadState", "pending"),
                      "parseState": previous.get("parseState", "unparsed")}
            repo.save(record)
            found[record["id"]] = record
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"stage": "query", "symbol": symbol, "reason": str(exc)})
    for record in found.values():
        previous = old.get(record["id"], {})
        try:
            if time.monotonic() >= deadline:
                raise ValueError("公告获取达到本次时间上限")
            raw = None
            if not force and record["url"] == previous.get("url") and record.get("sha256"):
                try:
                    raw = repo.original(record)
                except (OSError, ValueError):
                    pass
            if raw is None:
                raw = _download(client, record["url"])
                digest = repo.save_original(raw)
            else:
                digest = record["sha256"]
            if time.monotonic() >= deadline:
                raise ValueError("公告获取达到本次时间上限")
            if record.get("sha256") != digest:
                record.pop("days", None)
                record.pop("riskDays", None)
                record["parseState"] = "unparsed"
                record.pop("parserRevision", None)
            record.update(sha256=digest, observedAt=observation_clock(), downloadState="downloaded")
            if parse:
                from src.services.thesis_ledger_announcement_parser import PARSER_REVISION, parse_announcement
            if parse and record.get("parserRevision") != PARSER_REVISION:
                record.pop("detail", None)
                parse_announcement(record, raw)
                if record["parseState"] == "failed":
                    detail = record["detail"]
                    if previous.get("parseState") == "parsed" and previous.get("sha256"):
                        try:
                            repo.original(previous)
                            record.update(previous)
                            record["detail"] = detail
                        except (OSError, ValueError):
                            pass
                    errors.append({"stage": "parse", "id": record["id"], "reason": detail})
        except (requests.RequestException, ValueError, OSError) as exc:
            if previous.get("sha256"):
                try:
                    repo.original(previous)
                    record.update(previous)
                except (ValueError, OSError):
                    record["downloadState"] = "failed"
            else:
                record["downloadState"] = "failed"
            record["detail"] = f"原文下载失败：{exc}"
            errors.append({"stage": "download", "id": record["id"], "reason": str(exc)})
        repo.save(record)
    return {"matched": len(found), "downloaded": sum(r["downloadState"] == "downloaded" for r in found.values()),
            "parsed": sum(r["parseState"] == "parsed" for r in found.values()), "truncated": truncated, "errors": errors}


def _metadata(item, symbol):
    identity, path = str(item["announcementId"]), item["adjunctUrl"]
    code = item["secCode"]
    exchange = "SZ"
    if code[0] in "48" or code.startswith("920"):
        exchange = "BJ"
    elif code[0] in "69":
        exchange = "SH"
    if symbol != f"{code}.{exchange}":
        raise ValueError("公告证券代码与请求交易所不匹配")
    if not re.fullmatch(r"\d+", identity) or not re.fullmatch(r"finalpage/\d{4}-\d{2}-\d{2}/\d+\.PDF", path, re.I):
        raise ValueError("公告原文身份或地址无效")
    title = html.unescape(re.sub(r"<[^>]+>", "", item["announcementTitle"]))
    return {"id": identity, "symbol": symbol, "title": title,
            "url": "https://static.cninfo.com.cn/" + path,
            "publishedDate": datetime.fromtimestamp(item["announcementTime"] / 1000, ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "fetchedAt": observation_clock(), "uses": announcement_uses(title)}


def _download(client, url):
    with client.get(url, headers=HEADERS, timeout=25, stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 10 * 1024 * 1024:
                raise ValueError("公告原件超过10MiB")
            chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.startswith(b"%PDF-"):
        raise ValueError("公告原件不是PDF")
    return raw
