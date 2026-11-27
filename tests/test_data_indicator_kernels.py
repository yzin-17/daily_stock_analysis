"""纯指标提取的固定旧基线、原生兼容和独立导入验证。"""

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from src.data.technical_indicators import calculate_macd, calculate_moving_averages, calculate_rsi
from src.services.technical_indicator_series import build_indicator_points

FIXTURE = json.loads((Path(__file__).parent / "fixtures/data_indicators_legacy_v1.json").read_text())


def normalized(value):
    if isinstance(value, float):
        return round(value, 12) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def digest(value):
    payload = json.dumps(normalized(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def frame_for(case):
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=len(case["closes"])).strftime("%Y-%m-%d"),
        "close": pd.Series(case["closes"], dtype=float),
    })


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_all_kernel_values_and_formal_outputs_match_pre_extraction_baseline(case):
    frame = frame_for(case)
    unchanged = frame.copy(deep=True)
    params = case["parameters"]
    calculated = calculate_moving_averages(frame)
    calculated = calculate_macd(calculated, fast=params["fast"], slow=params["slow"], signal=params["signal"])
    calculated = calculate_rsi(calculated, periods=(params["short"], params["mid"], params["long"]))
    rows = calculated[case["columns"]].values.tolist()
    baseline = np.asarray(case["values"], dtype=float).reshape(-1, len(case["columns"]))
    actual = np.asarray(rows, dtype=float).reshape(-1, len(case["columns"]))
    # 容许跨架构的末位浮点误差，缺失值位置和完整输入指纹仍逐项核对。
    np.testing.assert_allclose(actual, baseline, rtol=0, atol=1e-11, equal_nan=True)
    for name, expected in case["formal"].items():
        _, points, fingerprint, row_fingerprints = build_indicator_points(frame, name, params, str)
        assert [point["timestamp"] for point in points] == frame["date"].tolist()
        assert [point["inputFingerprint"] for point in points] == row_fingerprints
        assert all(set(point["values"]) == set(expected["projections"]) for point in points)
        for field, (column, first_defined) in expected["projections"].items():
            values = baseline[:, case["columns"].index(column)].copy()
            values[:first_defined] = np.nan
            actual_values = np.asarray([point["values"][field] for point in points], dtype=float)
            np.testing.assert_allclose(actual_values, values, rtol=0, atol=1e-11, equal_nan=True)
        assert fingerprint == expected["inputFingerprint"]
        assert digest(row_fingerprints) == expected["rowFingerprintDigest"]
    pd.testing.assert_frame_equal(frame, unchanged)


@pytest.mark.parametrize("name", ["MA", "MACD", "RSI"])
def test_prefix_calculation_keeps_same_formal_values_and_input_identity(name):
    case = next(case for case in FIXTURE["cases"] if case["name"] == "mixed")
    frame = frame_for(case)
    _, all_points, _, _ = build_indicator_points(frame, name, case["parameters"], str)
    for count in (1, 6, 19, 34, 60, 73):
        _, points, _, _ = build_indicator_points(frame.iloc[:count], name, case["parameters"], str)
        assert normalized(points) == normalized(all_points[:count])


ISOLATED_PROBE = r"""
import importlib.abc, socket, sys
forbidden = ("src.stock_analyzer", "src.config", "src.schemas", "src.agent", "src.llm",
             "src.report_language", "src.notification", "litellm", "openai", "data_provider")
class BlockProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("pure data indicators imported " + fullname)
sys.meta_path.insert(0, BlockProducts())
def deny(self, address):
    raise AssertionError("pure data indicators attempted a network connection")
socket.socket.connect = deny
import pandas as pd
from src.services.technical_indicator_series import build_indicator_points, normalize_indicator_parameters
frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=70).astype(str),
                      "close": [10 + i / 4 for i in range(70)]})
for name in ("MA", "MACD", "RSI"):
    _, points, _, _ = build_indicator_points(frame, name, normalize_indicator_parameters(name, None), str)
    assert len(points) == 70
assert not any(name == p or name.startswith(p + ".") for name in sys.modules for p in forbidden)
print("pure-data-indicators-ok")
"""


def test_formal_series_runs_without_product_config_or_sdks():
    result = subprocess.run(
        [sys.executable, "-c", ISOLATED_PROBE], cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr[-1600:]
    assert result.stdout.strip() == "pure-data-indicators-ok"
