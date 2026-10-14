from data_provider.cninfo_announcements import acquire_announcements, _metadata
from src.services.thesis_ledger_announcements import AnnouncementRepository
import requests
import pytest


def item():
    return {"secCode": "000881", "announcementId": "123", "announcementTitle": "<em>停牌</em>公告",
            "adjunctUrl": "finalpage/2023-08-21/123.PDF", "announcementTime": 1692547200000}


def test_publication_date_uses_china_timezone():
    assert _metadata(item(), "000881.SZ")["publishedDate"] == "2023-08-21"
    with pytest.raises(ValueError, match="交易所不匹配"):
        _metadata(item(), "000881.SH")
    assert _metadata({**item(), "secCode": "920000"}, "920000.BJ")["symbol"] == "920000.BJ"


def test_highlighted_title_preserves_announcement_use():
    record = _metadata({**item(), "announcementTitle": "公司<em>复</em>牌暨风险<em>警示</em>公告"}, "000881.SZ")
    assert record["title"] == "公司复牌暨风险警示公告"
    assert record["uses"] == ["suspension", "risk"]


def test_truncated_pagination_and_failed_original_preserve_metadata(tmp_path):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"announcements": [item()], "hasMore": True}
    class Session:
        def post(self, url, **kwargs):
            if "topSearch" in url:
                class Search(Response):
                    def json(self): return [{"code": "000881", "category": "A股", "orgId": "gssz0000881"}]
                return Search()
            assert kwargs["data"]["stock"] == "000881,gssz0000881"
            return Response()
        def get(self, *args, **kwargs): raise requests.HTTPError("503")
    repo = AnnouncementRepository(tmp_path)
    result = acquire_announcements(["000881.SZ"], "2023-08-21", "2023-08-21", max_pages=1, session=Session(), repository=repo)
    assert result["truncated"] is True
    assert result["matched"] == 1
    assert result["downloaded"] == 0
    assert repo.query("000881")["items"][0]["downloadState"] == "failed"


def test_valid_original_is_reused_and_failed_refresh_preserves_facts(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"announcements": [item()], "hasMore": False}
    class Session:
        def post(self, url, **kwargs):
            if "topSearch" in url:
                class Search(Response):
                    def json(self): return [{"code": "000881", "category": "A股", "orgId": "gssz0000881"}]
                return Search()
            return Response()
        def get(self, *args, **kwargs): raise requests.HTTPError("503")
    repo = AnnouncementRepository(tmp_path)
    raw = b"%PDF-existing"
    existing = {**_metadata(item(), "000881.SZ"), "sha256": repo.save_original(raw),
                "downloadState": "downloaded", "parseState": "parsed", "days": [{"date": "2023-08-21", "state": "suspended"}]}
    repo.save(existing)
    result = acquire_announcements(["000881.SZ"], "2023-08-01", "2023-08-31", repository=repo, session=Session())
    assert result["downloaded"] == 1 and not result["errors"]
    result = acquire_announcements(["000881.SZ"], "2023-08-01", "2023-08-31", repository=repo, session=Session(), force=True)
    assert result["downloaded"] == 1 and result["errors"][0]["stage"] == "download"
    assert repo.records()[0]["days"] == existing["days"]
    assert repo.original(existing) == raw
    def failed_parser(record, raw):
        record.update(parseState='failed', detail='原文解析失败：模拟解析器升级失败')
    monkeypatch.setattr('src.services.thesis_ledger_announcement_parser.parse_announcement', failed_parser)
    result = acquire_announcements(["000881.SZ"], "2023-08-01", "2023-08-31", repository=repo, session=Session(), parse=True)
    assert result['errors'][0]['stage'] == 'parse'
    assert repo.records()[0]['parseState'] == 'parsed'
    assert repo.records()[0]['days'] == existing['days']


def test_completed_empty_search_is_cached_but_never_creates_status(tmp_path):
    calls = []
    class Response:
        def __init__(self, value): self.value = value
        def raise_for_status(self): pass
        def json(self): return self.value
    class Session:
        def post(self, url, **kwargs):
            calls.append(url)
            if 'topSearch' in url:
                return Response([{"code": "000881", "category": "A股", "orgId": "gssz0000881"}])
            return Response({"announcements": None, "totalAnnouncement": 0, "hasMore": False})
        def get(self, *a, **k): raise AssertionError('空检索没有原文')
    repo = AnnouncementRepository(tmp_path)
    for _ in range(2):
        result = acquire_announcements(['000881.SZ'], '2023-08-01', '2023-08-31', repository=repo, session=Session())
        assert result['matched'] == 0 and not result['errors']
    assert len(calls) == 4
    assert repo.records() == []
