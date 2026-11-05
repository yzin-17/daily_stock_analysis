"""公开来源仍受现有精确配置和总预算约束，不附带账号凭据。"""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from data_provider.market_reference_bindings import guarded
from data_provider.market_reference_public_common import PublicRead
import data_provider.market_reference_public_common as common
from data_provider.market_reference_projection import http_bytes
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def test_saved_custom_transport_configuration_is_not_ignored(tmp_path):
    path = tmp_path / 'config.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE thesis_ledger_provider_config (provider_id TEXT, settings_json TEXT)')
        db.execute('INSERT INTO thesis_ledger_provider_config VALUES (?, ?)', ('akshare', json.dumps({'headers': {'X-Test': 'configured'}})))
    called = []
    read = guarded(lambda *args: called.append(args))
    with pytest.raises(CapabilityContractError):
        read(SimpleNamespace(dataset='repo-fr'), SimpleNamespace(database_path=str(path)))
    assert not called


def test_multiple_public_requests_share_one_total_budget(monkeypatch):
    clock = iter([100, 101, 110])
    monkeypatch.setattr(common, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    budgets = []
    monkeypatch.setattr(common, 'http_bytes', lambda url, params, headers, seconds, **kwargs: budgets.append(seconds) or b'')
    read = PublicRead(30)
    read.read('https://www.pbc.gov.cn/')
    read.read('https://www.pbc.gov.cn/next')
    assert budgets == [29, 20]


def test_public_http_disables_implicit_netrc_and_does_not_retry(monkeypatch):
    called = []

    class Response:
        status_code = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def raise_for_status(self):
            return None
        def iter_content(self, **kwargs):
            yield b'public'

    def get(url, **options):
        called.append(options)
        request = SimpleNamespace(headers={})
        assert options['auth'](request) is request and request.headers == {}
        return Response()

    monkeypatch.setattr('requests.get', get)
    assert http_bytes('https://www.chinamoney.com.cn/', {}, {}, 10) == b'public'
    assert len(called) == 1 and called[0]['allow_redirects'] is False
