"""组合请求中的已知状态，窗口中没有资料的日期由既有读取器保留未知。"""

from src.services.thesis_ledger_announcements import AnnouncementRepository


def merge_status_day(previous, value):
    merged = {**previous}
    for field in ("state", "riskWarning", "dailyPriceLimit"):
        if field not in value:
            continue
        new, old = value[field], previous.get(field)
        if field == "riskWarning" and old:
            new = {**new}
            for kind in ("delisting", "other"):
                if old[kind] != "unknown" and new[kind] != "unknown" and old[kind] != new[kind]:
                    raise ValueError("公告库中存在矛盾风险事实")
                if new[kind] == "unknown":
                    new[kind] = old[kind]
        elif old is not None and old != "unknown" and new != "unknown" and old != new:
            raise ValueError("公告库中存在矛盾状态事实")
        if new != "unknown":
            merged[field] = new
    merged["sourceDocumentIds"] = sorted(set(previous["sourceDocumentIds"] + value["sourceDocumentIds"]))
    return merged


def library_status_pack(seed, symbol, start, end, source, repository=None):
    from src.services.thesis_ledger_trading_status import UPSTREAM_SOURCE, RISK_UPSTREAM_SOURCE
    repo = repository or AnnouncementRepository()
    docs, by_day = {}, {}

    def add(day, references):
        value = {**day, "sourceDocumentIds": references}
        previous = by_day.get(day["date"])
        if previous:
            value = merge_status_day(previous, value)
        by_day[day["date"]] = value

    if seed["symbol"] == symbol:
        docs.update({item["id"]: item for item in seed["sourceDocuments"]})
        for day in seed["days"]:
            if start <= day["date"] <= end:
                add(day, day["sourceDocumentIds"])
    records = [r for r in repo.records() if r["symbol"] == symbol]
    for record in records:
        if source not in (UPSTREAM_SOURCE, RISK_UPSTREAM_SOURCE) or record.get("parseState") != "parsed":
            continue
        facts = record.get("days", [])
        if source == RISK_UPSTREAM_SOURCE:
            facts = facts + record.get("riskDays", [])
        days = [day for day in facts if start <= day["date"] <= end]
        if not days:
            continue
        repo.original(record)
        identity = f"cninfo-{record['id']}"
        docs[identity] = {"id": identity, "title": record["title"], "url": record["url"],
                          "kind": "issuerDisclosure", "publishedDate": record["publishedDate"],
                          "sha256": record["sha256"], "observedAt": record["observedAt"]}
        for day in days:
            add({key: day[key] for key in ("date", "state", "riskWarning", "dailyPriceLimit") if key in day}, [identity])
    if seed["symbol"] != symbol and not records:
        return None
    return {"datasetId": f"cn-status-library-{symbol}", "symbol": symbol, "upstreamSource": source,
            "coverage": {"start": start, "end": end}, "sourceDocuments": list(docs.values()), "days": list(by_day.values())}
