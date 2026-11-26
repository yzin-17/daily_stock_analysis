"""汇率原表兼容、精确缓存边界和无原生业务导入的正式 HTTP。"""

from contextlib import closing
from datetime import date, datetime
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.fx_rates import fetch_fx_rate_from_yfinance
from src.data.fx_rate_repository import FxRateRepository
from src.data_config import DataServiceConfig
from src.services.fx_rate_service import build_fx_rates

DAY = date(2026, 10, 16)
LEGACY_SQL = (Path(__file__).parent / "fixtures/fx_cache_legacy_v1.sql").read_text()


def legacy_database(path):
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(LEGACY_SQL)
        connection.execute("CREATE TABLE legacy_records (id INTEGER PRIMARY KEY, payload TEXT)")
        connection.execute("INSERT INTO legacy_records VALUES (1, 'synthetic-preserved-record')")
        connection.executemany(
            "INSERT INTO portfolio_fx_rates "
            "(id,from_currency,to_currency,rate_date,rate,source,is_stale,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            [(41, "USD", "CNY", "2026-10-16", 7.25, "legacy-source", 0, "2026-10-16 08:30:00.000000"),
             (42, "USD", "CNY", "2026-10-17", 8.0, "future-record", 0, None)],
        )
        connection.commit()


def schema(connection):
    columns = connection.execute("PRAGMA table_info(portfolio_fx_rates)").fetchall()
    indexes = sorted((bool(row[2]), tuple(item[2] for item in connection.execute(
        f'PRAGMA index_info("{row[1]}")'
    ))) for row in connection.execute("PRAGMA index_list(portfolio_fx_rates)"))
    return columns, indexes


def test_old_table_reopens_in_place_and_upsert_preserves_id_schema_and_other_records(tmp_path):
    path = tmp_path / "shared.db"
    legacy_database(path)
    with closing(sqlite3.connect(path)) as connection:
        original_schema = schema(connection)
        original_tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    config = DataServiceConfig(database_path=str(path))
    with closing(FxRateRepository(config=config)) as repo:
        row = repo.get_latest_fx_rate(from_currency="USD", to_currency="CNY", as_of=DAY)
        assert row.id == 41 and row.rate == 7.25 and row.source == "legacy-source"
        assert row.updated_at == datetime(2026, 10, 16, 8, 30)
        repo.save_fx_rate(from_currency="USD", to_currency="CNY", rate_date=DAY, rate=7.3, source="controlled")
    with closing(FxRateRepository(config=config)) as reopened:
        row = reopened.get_latest_fx_rate(from_currency="USD", to_currency="CNY", as_of=DAY)
        assert row.id == 41 and row.rate == 7.3 and row.updated_at.tzinfo is None
        assert reopened.get_latest_fx_rate(from_currency="USD", to_currency="CNY", as_of=date(2026, 10, 15)) is None
    with closing(sqlite3.connect(path)) as connection:
        assert schema(connection) == original_schema
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == original_tables
        assert connection.execute("SELECT * FROM legacy_records").fetchall() == [(1, "synthetic-preserved-record")]
        assert connection.execute("SELECT COUNT(*) FROM portfolio_fx_rates").fetchone()[0] == 2


def test_new_database_only_initializes_fx_table_with_compatible_schema(tmp_path):
    path = tmp_path / "new.db"
    repo = FxRateRepository(config=DataServiceConfig(database_path=str(path)))
    with closing(repo):
        repo.save_fx_rate(from_currency="HKD", to_currency="CNY", rate_date=DAY, rate=0.92)
    repo.close()
    with closing(sqlite3.connect(path)) as actual, closing(sqlite3.connect(":memory:")) as legacy:
        legacy.executescript(LEGACY_SQL)
        assert schema(actual) == schema(legacy)
        assert actual.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("portfolio_fx_rates",)]


@pytest.mark.parametrize("age,expected", [(0, True), (7, True), (8, False)])
@pytest.mark.parametrize("inverse", [False, True])
def test_cache_date_age_inverse_and_failed_refresh_boundaries(tmp_path, age, inverse, expected):
    from datetime import timedelta
    with closing(FxRateRepository(str(tmp_path / "rates.db"), config=DataServiceConfig())) as repo:
        source, target = ("CNY", "USD") if inverse else ("USD", "CNY")
        repo.save_fx_rate(from_currency=source, to_currency=target, rate_date=DAY - timedelta(days=age),
                          rate=7.25, source="controlled", is_stale=True)
        fetch = Mock(return_value=None)
        result = build_fx_rates("CNY", ["CNY", "USD"], DAY, repo=repo, fetch=fetch)
        identity, rate = result["rates"]
        assert identity["provider"] == "identity" and identity["rate"] == 1
        assert rate["available"] is expected and rate["ageDays"] == age
        if expected:
            assert rate["rate"] == pytest.approx(1 / 7.25 if inverse else 7.25)
            assert rate["stale"] is True and "fetchedAt" not in rate
            fetch.assert_not_called()
        else:
            assert rate["freshness"] == "unavailable" and "rate" not in rate
            fetch.assert_called_once_with(from_currency="USD", to_currency="CNY", as_of_date=DAY)


def test_cold_fetch_saves_then_hot_cache_does_not_query_again(tmp_path):
    with closing(FxRateRepository(str(tmp_path / "rates.db"), config=DataServiceConfig())) as repo:
        fetch = Mock(return_value=7.25)
        cold = build_fx_rates("CNY", ["USD"], DAY, repo=repo, fetch=fetch)["rates"][0]
        hot = build_fx_rates("CNY", ["USD"], DAY, repo=repo, fetch=fetch)["rates"][0]
        assert cold["rate"] == hot["rate"] == 7.25
        assert cold["provider"] == hot["provider"] == "yfinance"
        assert "fetchedAt" in cold and "fetchedAt" not in hot
        fetch.assert_called_once()


@pytest.mark.parametrize("failure", [None, RuntimeError("controlled-source-failure")])
def test_missing_source_does_not_invent_rate(tmp_path, failure):
    with closing(FxRateRepository(str(tmp_path / "rates.db"), config=DataServiceConfig())) as repo:
        fetch = Mock(side_effect=failure, return_value=None)
        rate = build_fx_rates("CNY", ["USD"], DAY, repo=repo, fetch=fetch)["rates"][0]
        assert rate["available"] is False and rate["ageDays"] is None and "rate" not in rate
        assert repo.get_latest_fx_rate(from_currency="USD", to_currency="CNY", as_of=DAY) is None


def test_yahoo_adapter_preserves_symbol_window_and_last_valid_close():
    history = Mock(return_value=pd.DataFrame({"Close": [7.2, 7.25, None]}))
    ticker = Mock(return_value=SimpleNamespace(history=history))
    assert fetch_fx_rate_from_yfinance(
        from_currency="USD", to_currency="CNY", as_of_date=DAY, sdk=SimpleNamespace(Ticker=ticker),
    ) == 7.25
    ticker.assert_called_once_with("USDCNY=X")
    history.assert_called_once_with(start="2026-10-09", end="2026-10-17", interval="1d", auto_adjust=False)


HTTP_PROBE = r"""
import asyncio, importlib.abc, json, socket, sys, types
forbidden = ("src.config", "src.storage", "src.repositories.portfolio_repo", "src.services.portfolio_service",
             "src.agent", "src.llm", "src.notification", "src.report_language", "litellm", "openai")
class BlockProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("data FX imported " + fullname)
sys.meta_path.insert(0, BlockProducts())
def deny(self, address):
    raise AssertionError("data FX attempted external connection")
socket.socket.connect = deny
from fastapi import FastAPI
import httpx
from api.thesis_ledger import router_v3
app = FastAPI()
app.include_router(router_v3, prefix="/api/v1")
async def run():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        path="/api/v1/thesis-ledger/market/fx-rates?baseCurrency=CNY&currencies=USD&asOf=2026-10-16"
        refused=await client.get(path)
        assert refused.status_code == 401
        result=await client.get(path, headers={"authorization":"Bearer fx-controlled-token"})
        assert result.status_code == 200, result.text
        row=next(row for row in result.json()["rates"] if row["fromCurrency"] == "USD")
        assert row["rate"] == 7.25 and row["rateDate"] == "2026-10-16"
        assert "fetchedAt" not in row
    assert not any(name == p or name.startswith(p + ".") for name in sys.modules for p in forbidden)
asyncio.run(run())
print("data-fx-http-ok")
"""


def test_actual_http_reopens_legacy_cache_without_native_business_modules(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    result = subprocess.run(
        [sys.executable, "-c", HTTP_PROBE], cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "ENV_FILE": str(env_file),
             "DATABASE_PATH": str(path), "THESIS_LEDGER_DSA_TOKEN": "fx-controlled-token",
             "THESIS_LEDGER_FIXTURE_MODE": "false"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr[-1800:]
    assert "data-fx-http-ok" in result.stdout
