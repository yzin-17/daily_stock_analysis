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
LEGACY_VALUES = json.loads(
    (Path(__file__).parent / "fixtures/data_indicators_legacy_values_v1.json").read_text()
)


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
        "date": pd.date_range("2025-01-01", periods=len(case["closes"])).astype(str),
        "close": pd.Series(case["closes"], dtype=float),
    })


def assert_legacy_values(actual, archived):
    # 历史基线按 12 位小数归一化。跨平台只容许该精度内的浮点差，保留缺值位置。
    np.testing.assert_allclose(
        np.asarray(actual, dtype=float), np.asarray(archived, dtype=float),
        atol=1e-12, rtol=0, equal_nan=True,
    )


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_all_kernel_values_and_formal_outputs_match_pre_extraction_baseline(case):
    frame = frame_for(case)
    unchanged = frame.copy(deep=True)
    params = case["parameters"]
    calculated = calculate_moving_averages(frame)
    calculated = calculate_macd(calculated, fast=params["fast"], slow=params["slow"], signal=params["signal"])
    calculated = calculate_rsi(calculated, periods=(params["short"], params["mid"], params["long"]))
    rows = calculated[case["columns"]].values.tolist()
    archived = LEGACY_VALUES[case["name"]]
    assert digest(archived["rows"]) == case["valuesDigest"]
    assert (archived["rows"][-1] if archived["rows"] else []) == case["last"]
    assert_legacy_values(rows, archived["rows"])
    for name, expected in case["formal"].items():
        _, points, fingerprint, row_fingerprints = build_indicator_points(frame, name, params, str)
        archived_points = []
        for point, values in zip(points, archived["series"][name], strict=True):
            assert point["values"].keys() == values.keys()
            assert_legacy_values([point["values"][key] for key in values], list(values.values()))
            archived_points.append({**point, "values": values})
        assert digest(archived_points) == expected["pointsDigest"]
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
