"""实际行情管理器的主备、日志与未知时间断言。"""

import logging
from types import SimpleNamespace
from unittest.mock import patch

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


class _DummyFetcher:
    def __init__(self, name: str, priority: int, result=None, error: Exception | None = None):
        self.name = name
        self.priority = priority
        self._result = result
        self._error = error

    def get_realtime_quote(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._result


def _make_quote(
    code: str = "600519",
    name: str = "贵州茅台",
    source: RealtimeSource = RealtimeSource.AKSHARE_EM,
    **overrides,
) -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(
        code=code,
        name=name,
        source=source,
        price=1688.0,
        change_pct=1.2,
        **overrides,
    )


@patch("src.data_config.get_data_config")
def test_manager_does_not_warn_when_fallback_source_succeeds(mock_get_config, caplog):
    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="efinance,akshare_em",
    )
    manager = DataFetcherManager(
        fetchers=[
            _DummyFetcher("EfinanceFetcher", 0, error=RuntimeError("efinance timeout")),
            _DummyFetcher("AkshareFetcher", 1, result=_make_quote()),
        ]
    )

    with caplog.at_level(logging.INFO):
        quote = manager.get_realtime_quote("600519")

    assert quote is not None
    assert quote.name == "贵州茅台"
    assert quote.fetched_at is not None
    assert quote.fallback_from == "efinance"
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert "所有数据源均不可用" not in caplog.text


@patch("src.data_config.get_data_config")
def test_manager_supplement_does_not_mark_fallback_from(mock_get_config):
    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="efinance,akshare_em",
    )
    primary = _make_quote(source=RealtimeSource.EFINANCE)
    supplement = _make_quote(source=RealtimeSource.AKSHARE_EM, volume_ratio=1.7)
    manager = DataFetcherManager(
        fetchers=[
            _DummyFetcher("EfinanceFetcher", 0, result=primary),
            _DummyFetcher("AkshareFetcher", 1, result=supplement),
        ]
    )

    quote = manager.get_realtime_quote("600519")

    assert quote is primary
    assert quote.fetched_at is not None
    assert quote.fallback_from is None
    assert quote.source == RealtimeSource.EFINANCE
    assert quote.volume_ratio == 1.7


@patch("src.data_config.get_data_config")
def test_manager_fallback_from_records_highest_priority_failed_source(mock_get_config):
    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="efinance,tushare,akshare_em",
    )
    manager = DataFetcherManager(
        fetchers=[
            _DummyFetcher("EfinanceFetcher", 0, error=RuntimeError("efinance timeout")),
            _DummyFetcher("TushareFetcher", 1, error=RuntimeError("tushare timeout")),
            _DummyFetcher("AkshareFetcher", 2, result=_make_quote()),
        ]
    )

    quote = manager.get_realtime_quote("600519")

    assert quote is not None
    assert quote.source == RealtimeSource.AKSHARE_EM
    assert quote.fallback_from == "efinance"
    assert quote.fetched_at is not None


@patch("src.data_config.get_data_config")
def test_manager_drops_invalid_provider_timestamp_before_return(mock_get_config):
    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="efinance",
        realtime_cache_ttl=600,
    )
    raw_quote = _make_quote(
        source=RealtimeSource.EFINANCE,
        provider_timestamp="not-a-date",
    )
    manager = DataFetcherManager(
        fetchers=[
            _DummyFetcher("EfinanceFetcher", 0, result=raw_quote),
        ]
    )

    quote = manager.get_realtime_quote("600519")

    assert quote is raw_quote
    assert quote.fetched_at is not None
    assert quote.provider_timestamp is None
    assert quote.stale_seconds is None
    assert quote.is_stale is None
