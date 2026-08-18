"""ThesisLedger Provider runtime 的 fallback 与序列来源回归。"""

from dataclasses import dataclass

import pytest

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerProviderRuntime,
)


@dataclass
class _Quote:
    """最小的归一化 Quote fixture。"""

    open_price: float = 99.0
    high: float = 102.0
    low: float = 98.0
    price: float = 100.0
    pre_close: float = 99.5
    volume: float = 1000.0
    amount: float = 100000.0


class _Row(dict):
    """提供 pandas 行对象所需的 `get` 语义。"""


class _Frame:
    """提供 Provider runtime 所需的最小 DataFrame 语义。"""

    empty = False
    columns = ("date", "open", "high", "low", "close", "volume", "amount")

    def __init__(self, rows, columns=None):
        """保存待返回的序列行。"""
        self._rows = rows
        self.columns = columns or self.columns

    def iterrows(self):
        """按 pandas `iterrows` 形状返回行。"""
        return iter(enumerate(self._rows))


def _store(tmp_path, routes):
    """建立带指定 Effective route 的独立 SQLite store。"""
    store = ThesisLedgerControlStore(str(tmp_path / "stock_analysis.db"))
    store.apply_policy(
        {
            "contractVersion": 1,
            "consumer": "thesis-ledger",
            "requestId": "runtime-test",
            "revision": 1,
            "enabled": True,
            "routes": routes,
        }
    )
    return store


def _quote_routes():
    """返回 Quote fallback 测试用 route。"""
    return {"REALTIME_QUOTE": {"STOCK": ["akshare", "efinance"]}}


def _bar_routes():
    """返回 Daily Bar 测试用 route。"""
    return {"DAILY_BAR": {"STOCK": ["akshare", "efinance"]}}


def test_quote_retries_transient_primary_once_then_uses_one_complete_fallback(tmp_path):
    """确认 transient failure 只重试一次并切换到完整的后备记录。"""

    class _TimeoutAdapter:
        """模拟持续 timeout 的主 Provider。"""

        calls = 0

        def get_realtime_quote(self, _symbol, *, source=None):
            """抛出 transient timeout。"""
            self.calls += 1
            raise TimeoutError("upstream timeout")

    class _HealthyAdapter:
        """模拟返回完整 Quote 的后备 Provider。"""

        calls = 0
        symbols = []

        def get_realtime_quote(self, symbol, *, source=None):
            """返回完整 Quote。"""
            self.calls += 1
            self.symbols.append(symbol)
            return _Quote()

    primary = _TimeoutAdapter()
    fallback = _HealthyAdapter()
    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, _quote_routes()),
        adapters={"akshare": primary, "efinance": fallback},
    )

    quote, provider, fallback_used = runtime.quote("600519.SH")

    assert quote is not None
    assert provider == "efinance"
    assert fallback_used is True
    assert primary.calls == 2
    assert fallback.calls == 1
    assert fallback.symbols == ["600519"]


def test_bars_returns_one_complete_frame_and_uses_route_provider_identity(tmp_path):
    """确认 Bars 返回完整序列并使用 route Provider 而非 adapter source。"""

    frame = _Frame(
        [
            _Row(
                date="2025-01-01",
                open=99.0,
                high=102.0,
                low=98.0,
                close=100.0,
                volume=1000.0,
                amount=100000.0,
            ),
            _Row(
                date="2025-01-02",
                open=100.0,
                high=103.0,
                low=99.0,
                close=101.0,
                volume=1100.0,
                amount=110000.0,
            ),
        ]
    )

    class _Adapter:
        """模拟返回完整 Bars frame 的 Provider。"""

        symbols = []

        def get_daily_data(self, symbol, *, days):
            """返回带无关 source 标签的完整 Bars frame。"""
            assert days == 30
            self.symbols.append(symbol)
            return frame, "adapter-source-must-not-be-used-as-provider"

    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, _bar_routes()),
        adapters={"akshare": _Adapter()},
    )

    result, provider, fallback_used = runtime.bars("600519.SH", days=30)

    assert result is frame
    assert provider == "akshare"
    assert fallback_used is False
    assert runtime.adapters["akshare"].symbols == ["600519"]


def test_provider_smoke_uses_native_symbol_format(tmp_path):
    """确认真实 Provider smoke 不把 Contract 的交易所后缀传给适配器。"""

    class _Adapter:
        """模拟返回供 runtime smoke 校验的行情与日线数据源。"""

        quote_symbols = []
        quote_sources = []
        bar_symbols = []

        def get_realtime_quote(self, symbol, *, source=None):
            """记录原生代码与通道并返回最小合法 Quote。"""
            self.quote_symbols.append(symbol)
            self.quote_sources.append(source)
            return _Quote()

        def get_daily_data(self, symbol, *, days):
            """记录日线请求并返回空但结构完整的 frame。"""
            self.bar_symbols.append((symbol, days))
            return _Frame([]), "adapter-source"

    adapter = _Adapter()
    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {}),
        adapters={"akshare": adapter},
    )

    assert runtime.smoke("akshare", "REALTIME_QUOTE")["status"] == "healthy"
    assert runtime.smoke("akshare", "DAILY_BAR")["status"] == "healthy"
    assert adapter.quote_symbols == ["600519"]
    assert adapter.quote_sources == ["sina"]
    assert adapter.bar_symbols == [("600519", 5)]


def test_bars_accepts_direct_fetcher_frame_result(tmp_path):
    """确认直接调用 BaseFetcher 时的 DataFrame 返回值不会被错误解包。"""

    frame = _Frame(
        [
            _Row(
                date="2025-01-01",
                open=99.0,
                high=102.0,
                low=98.0,
                close=100.0,
                volume=1000.0,
                amount=100000.0,
            )
        ]
    )

    class _Adapter:
        """模拟返回直接 frame 的日线 Provider。"""

        def get_daily_data(self, _symbol, *, days):
            """校验 facade 传入的天数并返回 fixture frame。"""
            assert days == 30
            return frame

    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, _bar_routes()),
        adapters={"akshare": _Adapter()},
    )

    result, provider, fallback_used = runtime.bars("600519.SH", days=30)

    assert result is frame
    assert provider == "akshare"
    assert fallback_used is False


def test_real_bars_facade_consumes_runtime_frame(monkeypatch, tmp_path):
    """确认非 fixture facade 能消费 runtime 返回的 frame。"""

    frame = _Frame(
        [
            _Row(
                date="2025-01-01",
                open=99.0,
                high=102.0,
                low=98.0,
                close=100.0,
                volume=1000.0,
                amount=100000.0,
            ),
            _Row(
                date="2025-01-02",
                open=100.0,
                high=103.0,
                low=99.0,
                close=101.0,
                volume=1100.0,
                amount=110000.0,
            ),
        ]
    )

    class _Adapter:
        """模拟返回供 facade 转换的 Bars Provider。"""

        def get_daily_data(self, _symbol, *, days):
            """返回供 facade 转换的最小 Bars frame。"""
            return frame, "adapter-source-must-not-be-used-as-provider"

    database_path = str(tmp_path / "stock_analysis.db")
    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, _bar_routes()),
        adapters={"akshare": _Adapter()},
    )
    monkeypatch.setenv("DATABASE_PATH", database_path)
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")

    import api.thesis_ledger as contract
    import src.services.thesis_ledger_provider_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: runtime)

    rows = contract._real_bars("600519.SH", None, None, 1)

    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2025-01-02T00:00:00+00:00"
    assert rows[0]["provider"] == "akshare"
    assert rows[0]["symbol"] == "600519.SH"
    assert rows[0]["fallbackUsed"] is False


def test_fund_nav_history_switches_the_complete_sequence_on_invalid_primary(
    monkeypatch, tmp_path
):
    """确认 Fund NAV history 遇到非法主序列时整体切换且不混源。"""
    primary_frame = _Frame(
        [_Row(日期="2025-01-01", 单位净值=-1.0)],
        columns=("日期", "单位净值"),
    )
    fallback_frame = _Frame(
        [
            _Row(日期="2025-01-01", 单位净值=1.1),
            _Row(日期="2025-01-02", 单位净值=1.2),
        ],
        columns=("日期", "单位净值"),
    )
    calls = {"akshare": 0, "efinance": 0}

    def fetch(provider_id, _symbol, _adapter):
        """按 Provider 返回一个非法或完整的 NAV 序列。"""
        calls[provider_id] += 1
        return primary_frame if provider_id == "akshare" else fallback_frame

    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.ThesisLedgerProviderRuntime._fund_nav_from_provider",
        staticmethod(fetch),
    )
    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {"FUND_NAV_HISTORY": {"MUTUAL_FUND": ["akshare", "efinance"]}}),
        adapters={"akshare": object(), "efinance": object()},
    )

    result, provider, fallback_used = runtime.fund_nav_history("000001.OF")

    assert result is fallback_frame
    assert provider == "efinance"
    assert fallback_used is True
    assert calls == {"akshare": 1, "efinance": 1}


def test_fund_nav_history_rejects_non_ascending_sequence(monkeypatch, tmp_path):
    """历史净值日期倒序时必须拒绝整条序列，而不是返回部分数据。"""
    frame = _Frame(
        [
            _Row(日期="2025-01-02", 单位净值=1.2),
            _Row(日期="2025-01-01", 单位净值=1.1),
        ],
        columns=("日期", "单位净值"),
    )
    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {"FUND_NAV_HISTORY": {"MUTUAL_FUND": ["akshare"]}}),
        adapters={"akshare": object()},
    )

    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.ThesisLedgerProviderRuntime._fund_nav_from_provider",
        staticmethod(lambda _provider_id, _symbol, _adapter: frame),
    )

    with pytest.raises(ProviderCallError, match="严格升序"):
        runtime.fund_nav_history("000001.OF")


def test_provider_history_smoke_validates_complete_sequence(monkeypatch, tmp_path):
    """FUND_NAV_HISTORY smoke 必须校验非空、唯一、升序和正数净值。"""
    frame = _Frame(
        [
            _Row(日期="2025-01-01", 单位净值=1.1),
            _Row(日期="2025-01-02", 单位净值=1.2),
        ],
        columns=("日期", "单位净值"),
    )
    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {}),
        adapters={"akshare": object()},
    )
    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.ThesisLedgerProviderRuntime._fund_nav_from_provider",
        staticmethod(lambda _provider_id, _symbol, _adapter: frame),
    )

    result = runtime.smoke("akshare", "FUND_NAV_HISTORY")

    assert result["status"] == "healthy"
    assert result["readOnly"] is True


def test_control_projection_catalog_ack_and_tombstone_survive_store_reopen(tmp_path, monkeypatch):
    """确认 Control projection、Catalog ACK 和 tombstone 可跨 store 重开保留。"""

    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    database_path = str(tmp_path / "stock_analysis.db")
    store = ThesisLedgerControlStore(database_path)

    store.save_provider_config(
        "akshare",
        {"enabled": True, "settings": {}},
    )
    store.apply_policy(
        {
            "contractVersion": 1,
            "consumer": "thesis-ledger",
            "requestId": "persistence-test",
            "revision": 1,
            "enabled": True,
            "routes": _quote_routes(),
        }
    )
    snapshot = store.catalog_snapshot()
    assert store.catalog_ack({"generation": snapshot["generation"], "checksum": snapshot["checksum"]})[
        "acknowledged"
    ] is True
    store.remove_provider("akshare", {"requestId": "persistence-remove", "reason": "test"})

    reopened = ThesisLedgerControlStore(database_path)
    projection = reopened.policy_projection()
    registry = {item["providerId"]: item for item in reopened.provider_registry()}
    with reopened._connect() as connection:
        ack = connection.execute(
            "SELECT generation, checksum, cursor FROM thesis_ledger_catalog_ack WHERE consumer = ?",
            ("thesis-ledger",),
        ).fetchone()

    assert projection["desired"]["revision"] == 1
    assert registry["akshare"]["credentialConfigured"] is False
    assert registry["akshare"]["tombstone"]["reason"] == "test"
    assert ack["generation"] == snapshot["generation"]
    assert ack["checksum"] == snapshot["checksum"]
    assert ack["cursor"] == snapshot["cursor"]


def test_fixture_fund_nav_history_honors_contract_limit(monkeypatch):
    """确认 fixture history 遵守 Contract 的 3650 条上限而非提前截断。"""

    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")

    from api.thesis_ledger import _fixture_fund_nav_history

    assert len(_fixture_fund_nav_history("000001.OF", 120)) == 120
    assert len(_fixture_fund_nav_history("000001.OF", 5000)) == 3650
