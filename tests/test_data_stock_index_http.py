"""正式数据索引 HTTP 的缓存、种子、热路径及刷新契约。"""

import json
import os
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from api.data_app import create_app
from src.data import stock_index_loader as loader


def write_index(path, name, size=1):
    rows = [[f"{index:06d}.SZ", f"{index:06d}", name, "pinyin", "py", [], "CN", "stock", True, 100]
            for index in range(size)]
    path.write_text(json.dumps(rows, ensure_ascii=False))


@pytest.mark.parametrize("mode,expected", [
    ("cache-new", "远程缓存"), ("seed-new", "数据种子"), ("cache-missing", "数据种子"),
    ("invalid-cache", "数据种子"), ("hot-path", "数据种子"), ("all-missing", None),
])
def test_real_data_route_retains_selection_and_failure_behavior(tmp_path, monkeypatch, mode, expected):
    seed = tmp_path / "seed.json"
    cache = tmp_path / "cache.json"
    if mode != "all-missing":
        write_index(seed, "数据种子")
        os.utime(seed, (2000, 2000))
    if mode not in {"all-missing", "cache-missing", "hot-path"}:
        write_index(cache, "远程缓存", size=100)
        os.utime(cache, (3000 if mode == "cache-new" else 1000,) * 2)
    if mode == "invalid-cache":
        cache.write_text("not-json")
        os.utime(cache, (3000, 3000))
    loader.clear_stock_index_cache()
    monkeypatch.setattr(loader, "get_stock_index_candidate_paths", lambda: (cache, seed))
    monkeypatch.setattr(loader, "get_remote_stock_index_cache_path", lambda: cache)
    if mode == "hot-path":
        def unexpected(*args):
            raise AssertionError("热路径不得解析数据种子")
        monkeypatch.setattr(loader, "_load_stock_index_file", unexpected)
    app = create_app()
    schedule = Mock()
    app.state.schedule_stock_index_refresh = schedule
    response = TestClient(app).get("/stocks.index.json")
    schedule.assert_called_once_with("serve-stock-index")
    if expected is None:
        assert response.status_code == 404 and response.text == "stock index not found"
    else:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["cache-control"] == "no-cache"
        assert response.json()[0][2] == expected
