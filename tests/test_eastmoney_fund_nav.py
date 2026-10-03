"""净值分页失败不返回部分序列，TLS 校验与预算保持开启。"""

import json

import pytest

from data_provider import eastmoney_fund_nav as reader


def page(days, total=3, **changes):
    return {'Success': True, 'ErrCode': 0, 'ErrorCode': '0', 'TotalCount': total,
            'Datas': [{'FSRQ': d, 'DWJZ': '1.2'} for d in days], **changes}


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, **kwargs):
        yield self.body


def install(monkeypatch, pages):
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        assert url == reader.ENDPOINT
        assert kwargs['verify'] is True
        assert kwargs['stream'] is True
        assert all(0 < n <= 10 for n in kwargs['timeout'])
        return Response(pages[len(calls)-1])
    monkeypatch.setattr(reader, 'PAGE_SIZE', 2)
    monkeypatch.setattr(reader.requests, 'get', get)
    return calls


def test_complete_pages_are_published_once(monkeypatch):
    calls = install(monkeypatch, [page(['2026-01-03','2026-01-02']), page(['2026-01-01'])])
    frame = reader.read_fund_nav('000001')
    assert len(frame) == 3
    assert [c['data']['pageIndex'] for c in calls] == ['1','2']


@pytest.mark.parametrize('second', [
    page([], total=3), page(['2026-01-01'], total=4),
    page(['2026-01-02']), page(['2026-02-30']),
    page(['2026-01-01'], Success=False), page(['2026-01-01'], ErrorCode='1'),
    page(['2026-01-01'], total=True), page(['2026-01-01'], Datas=[{}]),
])
def test_invalid_later_page_rejects_whole_read(monkeypatch, second):
    install(monkeypatch, [page(['2026-01-03','2026-01-02']), second])
    with pytest.raises(ValueError):
        reader.read_fund_nav('000001')


def test_response_size_limit(monkeypatch):
    install(monkeypatch, [page(['2026-01-03','2026-01-02'])])
    monkeypatch.setattr(reader, 'MAX_RESPONSE_BYTES', 8)
    with pytest.raises(ValueError, match='预算'):
        reader.read_fund_nav('000001')


def test_time_budget_prevents_first_request(monkeypatch):
    calls = install(monkeypatch, [])
    times = iter([0, 56])
    monkeypatch.setattr(reader.time, 'monotonic', lambda: next(times))
    with pytest.raises(TimeoutError):
        reader.read_fund_nav('000001')
    assert calls == []


def test_declared_empty_is_empty(monkeypatch):
    install(monkeypatch, [page([], total=0)])
    assert reader.read_fund_nav('000001').empty
