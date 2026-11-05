"""公开新闻身份、独立配置和真实受控检索的组合验收。"""

from hashlib import sha256
import json
import sqlite3

import pytest

from src.data_config import DataServiceConfig, DataConfigurationError
from src.services.thesis_ledger_news_contract_v1 import NewsInstrument, NewsRequest, NewsRoute
from src.services.thesis_ledger_news_instruments import resolve_public_news_instrument
from src.services.thesis_ledger_news_records import RawNewsRead
from src.services.thesis_ledger_news_service import search_news, NEWS_UPSTREAMS
from src.services.thesis_ledger_news_sources import news_settings, PROVIDERS
from tests import test_thesis_ledger_news_execution as execution_tests

server = execution_tests.server


def request(symbol="000001.SZ", market="CN", kind="STOCK", **kwargs):
    return NewsRequest(requestId="controlled-public-news", instrument=NewsInstrument(
        symbol=symbol, market=market, assetType=kind), **kwargs)


def route(provider="searxng"):
    return NewsRoute(providerId=provider, upstreamSource=NEWS_UPSTREAMS[provider],
                     policyRevision="policy:controlled", routeRevision="route:controlled")


def forbidden(*_args, **_kwargs):
    pytest.fail("拒绝路径不应执行取数或加载环境凭据")


def catalog(path, rows, *, checksum=None):
    items = sorted(rows, key=lambda item: (item["canonicalCode"], item["market"], item["instrumentType"]))
    encoded = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE thesis_ledger_catalog_generation (generation, cursor, complete, items_json, checksum)")
        connection.execute("INSERT INTO thesis_ledger_catalog_generation VALUES (1, 'generation:1', 1, ?, ?)",
                           (encoded, checksum or sha256(encoded.encode()).hexdigest()))
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("symbol,market,name", [
    ("000001.SZ", "CN", "平安银行"), ("00700.HK", "HK", "腾讯控股"), ("AAPL.US", "US", "APPLE"),
])
def test_real_data_asset_verifies_exact_public_stock_identity_without_creating_database(tmp_path, symbol, market, name):
    path = tmp_path / "absent.db"
    identity = resolve_public_news_instrument(request(symbol, market).instrument, path)
    assert identity.symbol == symbol and identity.market == market and identity.name == name
    assert symbol in identity.query() and name in identity.query()
    assert not path.exists()


def test_catalog_checksums_and_asset_type_take_precedence_over_stock_name_index(tmp_path):
    path = tmp_path / "catalog.db"
    catalog(path, [{"canonicalCode": "510300", "market": "SH", "instrumentType": "ETF", "displayName": "沪深300ETF"},
                   {"canonicalCode": "000001", "market": "OF", "instrumentType": "MUTUAL_FUND", "displayName": "同名基金"}])
    before = path.read_bytes()
    assert resolve_public_news_instrument(request("510300.SH", kind="ETF").instrument, path).asset_type == "ETF"
    assert resolve_public_news_instrument(request("000001.OF", kind="MUTUAL_FUND").instrument, path).name == "同名基金"
    assert resolve_public_news_instrument(request("510300.SH").instrument, path) is None
    assert path.read_bytes() == before


def test_conflicting_public_type_and_corrupt_catalog_never_become_verified_identity(tmp_path):
    row = {"canonicalCode": "000001", "market": "SZ", "instrumentType": "STOCK", "displayName": "同名"}
    path = tmp_path / "conflict.db"
    catalog(path, [row, {**row, "instrumentType": "ETF"}])
    assert resolve_public_news_instrument(request().instrument, path) is None
    corrupt = tmp_path / "corrupt.db"
    catalog(corrupt, [row], checksum="0" * 64)
    with pytest.raises(DataConfigurationError, match="公开新闻标的目录读取失败"):
        resolve_public_news_instrument(request().instrument, corrupt)


def test_same_name_and_cross_market_rows_do_not_substitute_requested_identity(monkeypatch, tmp_path):
    from src.services import thesis_ledger_news_instruments as identities
    monkeypatch.setattr(identities, "_index_rows", lambda: [
        ["00700.HK", "00700", "同名", "", "", [], "HK", "stock", True],
        ["SAME", "SAME", "同名", "", "", [], "US", "stock", True],
    ])
    hk = resolve_public_news_instrument(request("00700.HK", "HK").instrument, tmp_path / "none.db")
    assert hk.symbol == "00700.HK" and hk.market == "HK"
    assert resolve_public_news_instrument(request("SAME.US", "US").instrument, tmp_path / "none.db").market == "US"
    assert resolve_public_news_instrument(request("OTHER.US", "US").instrument, tmp_path / "none.db") is None


@pytest.mark.parametrize("provider", PROVIDERS)
def test_explicit_credentials_never_fall_back_to_environment(provider):
    config = DataServiceConfig(tavily_api_keys=["controlled-private-environment"])
    settings = news_settings(provider, config=config, values={})
    assert not settings.configured()
    field = "baseUrls" if provider == "searxng" else "apiKeys"
    value = "https://search.example" if provider == "searxng" else "controlled-private-value"
    settings = news_settings(provider, config=config, values={field: json.dumps([value])})
    assert settings.configured() and value not in repr(settings)
    with pytest.raises(DataConfigurationError):
        news_settings(provider, values={field: json.dumps({"bad": "shape"})})


@pytest.mark.parametrize("code,kwargs", [
    ("ambiguous_instrument", {"identity_reader": lambda *_: None}),
    ("missing_credentials", {}),
])
def test_preflight_denials_have_no_upstream_attempts(tmp_path, code, kwargs):
    def missing(settings, *_args, **_kwargs):
        assert not settings.configured()
        from src.services.thesis_ledger_news_execution import execute_news_search
        return execute_news_search(settings, "公开身份", limit=5, days=7, request_budget_ms=10_000)
    result = search_news(request(), route(), values={}, database_path=tmp_path / "none.db",
                         config_reader=forbidden, executor=forbidden if code == "ambiguous_instrument" else missing,
                         **kwargs)
    assert result.status == "unavailable" and result.upstreamAttempts == 0
    assert {item.code for item in result.errors} == {code}


def test_config_failure_is_not_missing_credentials_and_does_not_leak_error(tmp_path):
    def broken():
        raise DataConfigurationError("controlled-private-config-value")
    result = search_news(request(), route(), config_reader=broken, executor=forbidden)
    assert result.upstreamAttempts == 0 and result.errors[0].code == "configuration_failure"
    assert "controlled-private-config-value" not in result.model_dump_json()
    invalid = search_news(request(), route(), values={"baseUrls": '["https://user:secret@host"]'},
                          database_path=tmp_path / "none.db", config_reader=forbidden, executor=forbidden)
    assert invalid.errors[0].code == "configuration_failure"


def test_actual_selected_search_composes_public_identity_and_fixed_contract(server, tmp_path):
    settings, state = server
    result = search_news(request(), route(), values={"baseUrls": json.dumps(settings.base_urls)},
                         database_path=tmp_path / "none.db", config_reader=forbidden)
    assert result.status == "available" and result.upstreamAttempts == len(state["calls"]) == 1
    assert result.items[0].relatedInstruments == [request().instrument]
    assert state["calls"][0]["q"] == ["000001.SZ 平安银行 中国市场 股票 新闻"]
    assert result.items[0].publishedAt == "2026-10-18T09:00:00+08:00"
    assert result.route == route()


def test_old_window_is_bounded_and_explicitly_incomplete(tmp_path):
    calls = []

    def execute(settings, query, **kwargs):
        calls.append((query, kwargs))
        return RawNewsRead(success=True, upstream_attempts=1, examined_count=0)

    result = search_news(request(publishedStart="2000-01-01T00:00:00Z"), route(),
                         values={"baseUrls": '["https://search.example"]'}, database_path=tmp_path / "none.db",
                         config_reader=forbidden, executor=execute)
    assert result.status == "partial" and result.coverage.complete is False
    assert result.coverage.missingReasons == ["search_window_exceeds_one_year"]
    assert calls[0][1]["days"] == 365
