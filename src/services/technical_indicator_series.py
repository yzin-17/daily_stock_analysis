"""Technical indicator date series built from the existing trend analyzer."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

import pandas as pd


def normalize_indicator_parameters(name: str, requested: Mapping[str, Any] | None) -> dict[str, int]:
    requested = requested or {}
    defaults = {
        "MA": {"period": 5},
        "MACD": {"fast": 12, "slow": 26, "signal": 9},
        "RSI": {"short": 6, "mid": 12, "long": 24},
    }[name]
    aliases = {
        "fast": ("fast", "macdFast"),
        "slow": ("slow", "macdSlow"),
        "signal": ("signal", "macdSignal"),
        "short": ("short", "rsiShort"),
        "mid": ("mid", "rsiMid"),
        "long": ("long", "rsiLong"),
        "period": ("period",),
    }
    result: dict[str, int] = {}
    for key, default in defaults.items():
        value: Any = default
        for alias in aliases[key]:
            if alias in requested:
                value = requested[alias]
                break
        if isinstance(value, bool) or not isinstance(value, int) or value < 2 or value > 200:
            raise ValueError(f"指标参数 {key} 必须是 2 到 200 之间的整数")
        result[key] = value
    if name == "MACD" and not result["fast"] < result["slow"]:
        raise ValueError("MACD fast 必须小于 slow")
    return result


def row_input_fingerprint(row: Any, timestamp: str) -> str:
    def number(value: Any) -> str | None:
        if value is None or pd.isna(value):
            return None
        return format(float(value), ".15g")

    payload = {
        "timestamp": timestamp,
        "open": number(row.get("open")),
        "high": number(row.get("high")),
        "low": number(row.get("low")),
        "close": number(row.get("close")),
        "volume": number(row.get("volume")),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


def series_input_fingerprint(fingerprints: list[str]) -> str:
    return hashlib.sha256("|".join(fingerprints).encode("utf-8")).hexdigest()[:24]


def build_indicator_points(
    frame: pd.DataFrame,
    name: str,
    parameters: Mapping[str, int],
    timestamp_builder: Callable[[Any], str],
    *,
    start: str | None = None,
    end: str | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], str, list[str]]:
    from src.stock_analyzer import StockTrendAnalyzer

    analyzer = StockTrendAnalyzer()
    analyzer.MACD_FAST = parameters.get("fast", analyzer.MACD_FAST)
    analyzer.MACD_SLOW = parameters.get("slow", analyzer.MACD_SLOW)
    analyzer.MACD_SIGNAL = parameters.get("signal", analyzer.MACD_SIGNAL)
    analyzer.RSI_SHORT = parameters.get("short", analyzer.RSI_SHORT)
    analyzer.RSI_MID = parameters.get("mid", analyzer.RSI_MID)
    analyzer.RSI_LONG = parameters.get("long", analyzer.RSI_LONG)
    calculated = frame.sort_values("date").reset_index(drop=True)
    calculated = analyzer._calculate_mas(calculated)
    calculated = analyzer._calculate_macd(calculated)
    calculated = analyzer._calculate_rsi(calculated)
    all_fingerprints = [
        row_input_fingerprint(row, timestamp_builder(row.get("date")))
        for _, row in calculated.iterrows()
    ]
    points: list[dict[str, Any]] = []
    for index, row in calculated.iterrows():
        timestamp = timestamp_builder(row.get("date"))
        day = timestamp[:10]
        if start and day < start[:10]:
            continue
        if end and day > end[:10]:
            continue
        row_fingerprint = all_fingerprints[index]
        if name == "MA":
            point_values = {
                "ma5": float(row["MA5"]) if index >= 4 and pd.notna(row["MA5"]) else None,
                "ma10": float(row["MA10"]) if index >= 9 and pd.notna(row["MA10"]) else None,
                "ma20": float(row["MA20"]) if index >= 19 and pd.notna(row["MA20"]) else None,
                "ma60": float(row["MA60"]) if index >= 59 and pd.notna(row["MA60"]) else None,
            }
        elif name == "MACD":
            warmup = analyzer.MACD_SLOW + analyzer.MACD_SIGNAL - 2
            point_values = {
                "dif": float(row["MACD_DIF"]) if index >= analyzer.MACD_SLOW - 1 else None,
                "dea": float(row["MACD_DEA"]) if index >= warmup else None,
                "histogram": float(row["MACD_BAR"]) if index >= warmup else None,
            }
        else:
            point_values = {
                f"rsi{period}": float(row[f"RSI_{period}"])
                if index >= period and pd.notna(row[f"RSI_{period}"])
                else None
                for period in (analyzer.RSI_SHORT, analyzer.RSI_MID, analyzer.RSI_LONG)
            }
        points.append({"timestamp": timestamp, "values": point_values, "inputFingerprint": row_fingerprint})
    return calculated, points, series_input_fingerprint(all_fingerprints), all_fingerprints
