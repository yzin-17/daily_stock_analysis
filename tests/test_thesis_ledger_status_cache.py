"""不同证券、区间、来源和修订的缓存互不命中。"""

from datetime import datetime, timedelta, timezone
import json

import pytest

from src.services.thesis_ledger_status_cache import HistoricalStatusCache

IDENTITY = {"symbol": "300757.SZ", "start": "2023-08-21", "end": "2023-09-05",
            "capability": "HISTORICAL_TRADING_STATUS", "provider": "baostock", "source": "baostock",
            "revision": "sdk1:normalizer1"}
VALUE = {"days": [{"date": "2023-08-21", "state": "suspended", "isST": "0"}]}


@pytest.mark.parametrize("field", list(IDENTITY))
def test_identity_isolates_every_dimension(tmp_path, field):
    cache = HistoricalStatusCache(tmp_path)
    cache.put(IDENTITY, VALUE)
    assert cache.get(IDENTITY) == VALUE
    assert cache.get({**IDENTITY, field: "other"}) is None


def test_unknown_does_not_replace_valid_facts(tmp_path):
    cache = HistoricalStatusCache(tmp_path)
    cache.put(IDENTITY, VALUE)
    cache.put(IDENTITY, {"days": [{"date": "2023-08-21", "state": "unknown", "isST": "unknown"}]})
    assert cache.get(IDENTITY) == VALUE


def test_expired_or_corrupted_cache_is_not_read_as_current(tmp_path):
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    cache = HistoricalStatusCache(tmp_path, clock=lambda: now)
    cache.put(IDENTITY, VALUE)
    cache.clock = lambda: now + timedelta(hours=25)
    assert cache.get(IDENTITY) is None
    cache.clock = lambda: now
    path = cache._path(IDENTITY)
    entry = json.loads(path.read_text())
    entry["value"]["days"][0]["state"] = "trading"
    path.write_text(json.dumps(entry))
    assert cache.get(IDENTITY) is None


def test_publish_failure_preserves_previous_cache(tmp_path, monkeypatch):
    cache = HistoricalStatusCache(tmp_path)
    cache.put(IDENTITY, VALUE)
    def fail(*args):
        raise OSError("publish failed")
    monkeypatch.setattr("src.services.thesis_ledger_status_cache.os.replace", fail)
    with pytest.raises(OSError):
        cache.put(IDENTITY, {"days": [{"date": "2023-08-21", "state": "trading", "isST": "0"}]})
    assert cache.get(IDENTITY) == VALUE
    assert not list(tmp_path.glob("*.tmp"))
