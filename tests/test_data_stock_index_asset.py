"""股票索引数据资产在没有产品目录时仍可读取和生成。"""

import json
from pathlib import Path
import sys

from src.data import stock_index_loader


def test_actual_data_seed_loads_without_web_or_static(tmp_path, monkeypatch):
    source = stock_index_loader.get_stock_index_candidate_paths()[1]
    seed = tmp_path / "src/data/assets/stocks.index.json"
    seed.parent.mkdir(parents=True)
    seed.write_bytes(source.read_bytes())
    monkeypatch.setattr(stock_index_loader, "__file__", str(tmp_path / "src/data/stock_index_loader.py"))
    monkeypatch.setattr(stock_index_loader, "get_remote_stock_index_cache_path",
                        lambda: tmp_path / "data/cache/stocks.index.json")
    stock_index_loader._clear_stock_index_cache_for_tests()
    try:
        assert stock_index_loader.find_existing_stock_index_path() == seed
        assert stock_index_loader.get_index_stock_name("000001.SZ")
        assert stock_index_loader.get_index_stock_name("7203.T")
        assert stock_index_loader.get_index_stock_name("005930.KS")
        assert not (tmp_path / "apps").exists()
        assert not (tmp_path / "static").exists()
    finally:
        stock_index_loader._clear_stock_index_cache_for_tests()


def test_csv_generator_writes_data_asset_without_client(tmp_path, monkeypatch):
    import importlib
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    generator = importlib.import_module("generate_index_from_csv")
    data = tmp_path / "data"
    data.mkdir()
    (data / "stock_list_a.csv").write_text("ts_code,symbol,name\n000001.SZ,000001,平安银行\n")
    (data / "stock_list_jp.csv").write_text("ts_code,name,enname\n7203.T,丰田汽车,Toyota\n")
    (data / "stock_list_kr.csv").write_text("ts_code,name,enname\n005930.KS,三星电子,Samsung\n")
    monkeypatch.setattr(generator, "__file__", str(tmp_path / "scripts/generate_index_from_csv.py"))
    monkeypatch.setattr(sys, "argv", ["generate_index_from_csv.py", "--source", "tushare"])
    assert generator.main() == 0
    payload = json.loads((tmp_path / "src/data/assets/stocks.index.json").read_text())
    assert {row[0] for row in payload} == {"000001.SZ", "7203.T", "005930.KS"}
    assert all(len(row) == 10 for row in payload)
    assert not (tmp_path / "apps").exists()
