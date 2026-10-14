from src.services.thesis_ledger_announcements import AnnouncementRepository


def record(identity="123", symbol="000881.SZ"):
    return {"id": identity, "symbol": symbol, "title": "停牌公告", "url": f"https://static.cninfo.com.cn/{identity}.PDF",
            "publishedDate": "2023-08-21", "uses": ["suspension"], "downloadState": "failed", "parseState": "unparsed"}


def test_failed_download_remains_visible_and_repeated_save_is_idempotent(tmp_path):
    repo = AnnouncementRepository(tmp_path)
    repo.save(record())
    repo.save(record())
    reopened = AnnouncementRepository(tmp_path)
    response = reopened.query("000881")
    assert response["total"] == 1
    assert response["items"][0]["downloadState"] == "failed"
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_pagination_and_original_seed_deduplication(tmp_path):
    repo = AnnouncementRepository(tmp_path)
    repo.save(record("123"))
    repo.save(record("124", "600774.SH"))
    first = repo.query(page=1, page_size=1)
    second = repo.query(page=2, page_size=1)
    assert first["total"] == 3
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert repo.query("000995")["items"][0]["uses"] == ["suspension", "risk"]
