"""ThesisLedger Provider runtime 的 fallback 与序列来源回归。"""

from dataclasses import dataclass

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


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

        def get_realtime_quote(self, _symbol):
            """抛出 transient timeout。"""
            self.calls += 1
            raise TimeoutError("upstream timeout")

    class _HealthyAdapter:
        """模拟返回完整 Quote 的后备 Provider。"""

        calls = 0

        def get_realtime_quote(self, _symbol):
            """返回完整 Quote。"""
            self.calls += 1
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
            )
        ]
    )

    class _Adapter:
        """模拟返回完整 Bars frame 的 Provider。"""

        def get_daily_data(self, _symbol, *, days):
            """返回带无关 source 标签的完整 Bars frame。"""
            assert days == 30
            return frame, "adapter-source-must-not-be-used-as-provider"

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
            )
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

    rows = contract._real_bars("600519.SH", None, None, 30)

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
