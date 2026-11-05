# -*- coding: utf-8 -*-
"""Tests for scripts.refresh_stock_index default fetch behavior."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

refresh_stock_index = importlib.import_module("refresh_stock_index")


def test_main_fetches_tushare_with_a_rk_by_default():
    with (
        patch.object(refresh_stock_index, "_has_tushare_token", return_value=True),
        patch.object(refresh_stock_index, "_run") as run,
        patch.object(refresh_stock_index, "_sync_static_index"),
    ):
        exit_code = refresh_stock_index.main([])

    assert exit_code == 0
    assert run.call_args_list[0].args[0] == [
        sys.executable,
        "scripts/fetch_tushare_stock_list.py",
        "--a-rk",
    ]
    assert run.call_args_list[1].args[0] == [
        sys.executable,
        "scripts/generate_index_from_csv.py",
        "--source",
        "tushare",
    ]


def test_static_sync_reads_data_seed_without_web_directory(tmp_path, monkeypatch):
    source = tmp_path / "src/data/assets/stocks.index.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'[["fixture","fixture","fixture"]]')
    target = tmp_path / "static/stocks.index.json"
    monkeypatch.setattr(refresh_stock_index, "DATA_INDEX_PATH", source)
    monkeypatch.setattr(refresh_stock_index, "STATIC_INDEX_PATH", target)
    refresh_stock_index._sync_static_index()
    assert target.read_bytes() == source.read_bytes()
    assert not (tmp_path / "apps").exists()
