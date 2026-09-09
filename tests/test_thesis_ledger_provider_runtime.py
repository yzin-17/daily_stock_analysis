"""ThesisLedger Provider runtime 的 fallback 与序列来源回归。"""

from dataclasses import dataclass

import pytest

from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS,
    ControlContractError,
    ThesisLedgerControlStore,
)
from src.services.thesis_ledger_provider_runtime import (
    _PROVIDER_ADAPTER_IMPORTS,
    ProviderCallError,
    ThesisLedgerDataGateway,
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
    ThesisLedgerGatewayError,
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


@dataclass
class _Chip:
    """最小的完整筹码摘要 fixture。"""

    avg_cost: float = 100.0
    profit_ratio: float = 0.5
    cost_70_low: float = 95.0
    cost_70_high: float = 105.0
    concentration_70: float = 0.1
    cost_90_low: float = 90.0
    cost_90_high: float = 110.0
    concentration_90: float = 0.2


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


def _chip_routes():
    """返回 CHIP_SUMMARY 摘要级 fallback 测试用 route。"""
    return {"CHIP_SUMMARY": {"STOCK": ["akshare", "efinance"]}}


def _fund_holdings_routes():
    """返回基金持仓披露测试用 route。"""
    return {"FUND_HOLDINGS": {"MUTUAL_FUND": ["akshare"]}}


def test_provider_registry_exposes_all_dsa_fetchers_for_routing(tmp_path):
    """Control 注册表应完整列出可参与路由配置的 DSA 数据源。"""
    registry = {
        item["providerId"]: item
        for item in ThesisLedgerControlStore(str(tmp_path / "stock_analysis.db")).provider_registry()
    }

    assert set(registry) == {
        "akshare",
        "efinance",
        "tencent",
        "tushare",
        "tickflow",
        "pytdx",
        "baostock",
        "yfinance",
        "longbridge",
        "finnhub",
        "alphavantage",
    }
    assert all("routeEligible" not in manifest for manifest in registry.values())
    assert set(_PROVIDER_ADAPTER_IMPORTS) == set(registry)
    assert registry["tencent"]["origin"] == "dsa"
    assert registry["tencent"]["capabilities"] == {
        "DAILY_BAR": ["ETF", "STOCK"]
    }
    assert registry["tencent"]["upstreamSources"] == [
        {"sourceId": "tencent", "displayName": "腾讯财经"}
    ]
    assert registry["yfinance"]["markets"] == ["CN", "HK", "JP", "KR", "TW", "US"]
    assert registry["tushare"]["configurationMode"] == "dsa_environment"


def test_mapping_quote_is_normalized_for_route_contract(tmp_path):
    """返回字典的内置 Provider 也能通过统一 Quote 契约。"""

    class _MappingQuoteAdapter:
        @staticmethod
        def get_realtime_quote(_symbol):
            return {
                "open": 99.0,
                "high": 102.0,
                "low": 98.0,
                "price": 101.0,
                "pre_close": 100.0,
                "volume": 1200,
                "amount": 121200,
            }

    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {"REALTIME_QUOTE": {"STOCK": ["pytdx"]}}),
        adapters={"pytdx": _MappingQuoteAdapter()},
    )

    quote, provider, fallback_used = runtime.quote("600519.SH")

    assert provider == "pytdx"
    assert fallback_used is False
    assert quote.price == 101.0
    assert quote.open_price == 99.0


def test_fund_holdings_uses_effective_route_without_weight_normalization(tmp_path):
    """基金持仓由声明能力的 Provider 返回，runtime 不改写披露权重。"""

    class _Adapter:
        calls = []

        def get_fund_holdings(self, symbol):
            self.calls.append(symbol)
            return _Frame(
                [
                    _Row(
                        股票代码="600519",
                        股票名称="贵州茅台",
                        占净值比例=8.0,
                        季度="2024年4季度",
                    )
                ],
                columns=("股票代码", "股票名称", "占净值比例", "季度"),
            )

    adapter = _Adapter()
    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(
            _store(tmp_path, _fund_holdings_routes()),
            adapters={"akshare": adapter},
        )
    )

    result = gateway.fund_holdings("000001.OF", request_id="holdings-request")

    assert result.provider == "akshare"
    assert result.fallback_used is False
    assert result.data._rows[0]["占净值比例"] == 8.0
    assert adapter.calls == ["000001"]


def test_chip_summary_uses_effective_route_and_preserves_fallback_metadata(tmp_path, monkeypatch):
    """筹码摘要失败时切换完整 Provider，不允许字段级混源。"""
    monkeypatch.setitem(
        PROVIDER_MANIFESTS["efinance"]["capabilities"],
        "CHIP_SUMMARY",
        ["STOCK"],
    )

    class _UnavailableAdapter:
        """模拟没有返回摘要的主 Provider。"""

        calls = 0

        def get_chip_distribution(self, _symbol):
            """返回空摘要，触发下一候选。"""
            self.calls += 1
            return None

    class _HealthyAdapter:
        """模拟返回完整摘要的后备 Provider。"""

        calls = 0
        symbols = []

        def get_chip_distribution(self, symbol):
            """返回单一来源的完整摘要。"""
            self.calls += 1
            self.symbols.append(symbol)
            return _Chip()

    primary = _UnavailableAdapter()
    fallback = _HealthyAdapter()
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataGateway

    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(
            _store(tmp_path, _chip_routes()),
            adapters={"akshare": primary, "efinance": fallback},
        )
    )

    result = gateway.chip_summary("600519.SH", request_id="chip-request")

    assert result.data.avg_cost == 100.0
    assert result.provider == "efinance"
    assert result.fallback_used is True
    assert result.route == ("akshare", "efinance")
    assert result.attempted_providers == ("akshare", "efinance")
    assert primary.calls == 1
    assert fallback.calls == 1
    assert fallback.symbols == ["600519"]


def test_chip_summary_rejects_non_stock_route_before_provider_call(tmp_path):
    """不支持的 CHIP_SUMMARY/InstrumentType 组合必须原子拒绝。"""
    with pytest.raises(ControlContractError, match="没有 Provider 支持"):
        _store(tmp_path, {"CHIP_SUMMARY": {"ETF": []}})

    store = _store(tmp_path, {"CHIP_SUMMARY": {"STOCK": ["akshare"]}})
    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": object()})
    with pytest.raises(ProviderCallError, match="STOCK 的 CHIP_SUMMARY"):
        runtime.execute_request(
            ThesisLedgerDataRequest(
                "CHIP_SUMMARY",
                "510300.SH",
                instrument_type="ETF",
                request_id="chip-invalid-route",
            )
        )


@pytest.mark.parametrize(
    ("enabled", "circuit"),
    [(False, "closed"), (True, "open")],
)
def test_chip_summary_skips_disabled_or_circuit_open_provider(
    tmp_path,
    enabled,
    circuit,
):
    """CHIP_SUMMARY 的非 eligible Provider 不得收到适配器请求。"""
    store = ThesisLedgerControlStore(str(tmp_path / "stock_analysis.db"))
    store.save_provider_config("akshare", {"enabled": enabled, "settings": {}})
    if circuit != "closed":
        store.record_health(
            "akshare",
            "CHIP_SUMMARY",
            "STOCK",
            state="degraded",
            circuit=circuit,
            consecutive_failures=3,
            error_code="transient_failure",
        )
    store.apply_policy(
        {
            "contractVersion": 1,
            "consumer": "thesis-ledger",
            "requestId": "chip-state-test",
            "revision": 1,
            "enabled": True,
            "routes": {"CHIP_SUMMARY": {"STOCK": ["akshare"]}},
        }
    )

    class _Adapter:
        """记录不应发生的摘要调用。"""

        calls = 0

        def get_chip_distribution(self, _symbol):
            self.calls += 1
            return _Chip()

    adapter = _Adapter()
    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(store, adapters={"akshare": adapter})
    )
    with pytest.raises(ThesisLedgerGatewayError) as raised:
        gateway.chip_summary("600519.SH", request_id="chip-state-request")

    assert raised.value.code == "NO_ELIGIBLE_PROVIDER"
    assert raised.value.request_id == "chip-state-request"
    assert adapter.calls == 0


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


def test_quote_derives_missing_previous_close_from_change_amount(tmp_path):
    """ETF 快照缺少昨收时，用同一快照的涨跌额补齐 Contract 字段。"""

    class _Adapter:
        """返回缺少 pre_close 但包含涨跌额的行情。"""

        def get_realtime_quote(self, _symbol, *, source=None):
            """模拟 AKShare ETF 快照的字段缺失。"""
            return type(
                "Quote",
                (),
                {
                    "open_price": 99.0,
                    "high": 102.0,
                    "low": 98.0,
                    "price": 100.0,
                    "pre_close": None,
                    "change_amount": 1.0,
                    "change_pct": 1.0,
                    "volume": 1000.0,
                    "amount": 100000.0,
                },
            )()

    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {"REALTIME_QUOTE": {"STOCK": ["akshare"]}}),
        adapters={"akshare": _Adapter()},
    )

    quote, provider, fallback_used = runtime.quote("600519.SH")

    assert quote.pre_close == 99.0
    assert provider == "akshare"
    assert fallback_used is False


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
    frame.attrs = {"upstream_source": "tencent"}

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


def test_daily_bar_request_passes_explicit_range_to_provider(tmp_path):
    """显式日线区间必须进入 Provider，不能只在返回后过滤。"""
    frame = _Frame(
        [
            _Row(
                date="2025-01-02",
                open=100.0,
                high=103.0,
                low=99.0,
                close=101.0,
                volume=1100.0,
                amount=110000.0,
            )
        ]
    )
    calls = []

    class _Adapter:
        """记录 runtime 传给 Provider 的完整日线范围。"""

        def get_daily_data(self, symbol, *, days, start_date, end_date):
            calls.append((symbol, days, start_date, end_date))
            return frame

    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {"DAILY_BAR": {"STOCK": ["akshare"]}}),
        adapters={"akshare": _Adapter()},
    )

    runtime.execute_request(
        ThesisLedgerDataRequest(
            "DAILY_BAR",
            "600519.SH",
            start="2025-01-01",
            end="2025-12-31",
            limit=365,
        )
    )

    assert calls == [("600519", 365, "2025-01-01", "2025-12-31")]


def test_v2_raw_daily_bar_request_uses_explicit_raw_provider_method(tmp_path):
    """V2 raw 请求走专用不复权入口，V1 仍调用默认日线入口。"""
    frame = _Frame(
        [
            _Row(
                date="2025-01-02",
                open=100.0,
                high=103.0,
                low=99.0,
                close=101.0,
                volume=1100.0,
                amount=110000.0,
            )
        ]
    )
    calls: list[tuple[str, str]] = []

    class _Adapter:
        def get_daily_data(self, symbol, *, days):
            calls.append(("v1", symbol))
            return frame

        def get_daily_data_v2_raw(self, symbol, *, days):
            calls.append(("v2-raw", symbol))
            return frame

    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {"DAILY_BAR": {"STOCK": ["akshare"]}}),
        adapters={"akshare": _Adapter()},
    )

    runtime.execute_request(ThesisLedgerDataRequest("DAILY_BAR", "600519.SH"))
    runtime.execute_request(
        ThesisLedgerDataRequest(
            "DAILY_BAR",
            "600519.SH",
            parameters={"priceMode": "raw"},
        )
    )

    assert calls == [("v1", "600519"), ("v2-raw", "600519")]


def test_tencent_daily_route_preserves_provider_and_actual_source(tmp_path):
    """腾讯独立路由既是 route Provider，也是实际日线通道。"""
    frame = _Frame(
        [
            _Row(
                date="2025-01-02",
                open=100.0,
                high=103.0,
                low=99.0,
                close=101.0,
                volume=1100.0,
                amount=110000.0,
            )
        ]
    )
    frame.attrs = {}

    class _Adapter:
        """模拟腾讯日线适配器。"""

        def get_daily_data(self, symbol, *, days):
            assert symbol == "510300"
            assert days == 30
            return frame

    runtime = ThesisLedgerProviderRuntime(
        _store(tmp_path, {"DAILY_BAR": {"ETF": ["tencent"]}}),
        adapters={"tencent": _Adapter()},
    )

    result = runtime.execute_request(
        ThesisLedgerDataRequest(
            "DAILY_BAR",
            "510300.SH",
            instrument_type="ETF",
            limit=30,
        )
    )

    assert result.provider == "tencent"
    assert result.fallback_used is False
    assert result.value.attrs["upstream_source"] == "tencent"


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
    frame.attrs = {"upstream_source": "tencent"}

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
    assert rows[0]["upstreamSource"] == "tencent"
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
