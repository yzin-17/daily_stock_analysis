"""派生周期指标的输入绑定与独立能力声明。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Callable

PERIOD_PROTOCOL = "market-period-indicator-v1"
PERIOD_TIMEFRAMES = ("5d", "1w", "1mo", "1y")
MAX_PERIOD_POINTS = 3650
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")


def period_indicator_capabilities() -> dict[str, Any]:
    """声明计算协议；不声明来源周期或历史预热已充足。"""
    return {
        "contractVersion": 3,
        "engineVersion": "dsa-indicator-v3",
        "calculation": "provided-points",
        "periodProtocol": PERIOD_PROTOCOL,
        "nativeTimeframes": ["1m", "1d"],
        "periods": [
            {
                "timeframe": timeframe,
                "kind": "derived",
                "maxInputPoints": MAX_PERIOD_POINTS,
                "parameterRange": {"minimum": 2, "maximum": 200},
                "indicators": ["MA", "MACD", "RSI"],
                "warmupRules": {
                    "MA": "max(period,60)-1",
                    "MACD": "slow+signal",
                    "RSI": "max(short,mid,long)+1",
                },
            }
            for timeframe in PERIOD_TIMEFRAMES
        ],
    }


def _instant(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_period_points(points: list[Any]) -> None:
    if not 1 <= len(points) <= MAX_PERIOD_POINTS:
        raise ValueError("派生指标计算点数必须为 1 到 3650")
    previous: str | None = None
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("派生指标点必须为对象")
        timestamp = point.get("timestamp")
        if not _instant(timestamp) or not _instant(point.get("availableAt")):
            raise ValueError("派生指标时间必须携带时区")
        if previous is not None and timestamp <= previous:
            raise ValueError("派生指标时间必须严格递增且唯一")
        previous = timestamp
        for key in ("open", "high", "low", "close", "volume", "amount"):
            number = point.get(key)
            if (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                or number < 0
            ):
                raise ValueError("派生指标 OHLCV/金额必须为有限非负数字")
        if (
            point["high"] < max(point["open"], point["close"], point["low"])
            or point["low"] > min(point["open"], point["close"], point["high"])
        ):
            raise ValueError("派生指标 OHLC 顺序无效")
        if point.get("completionStatus") not in ("complete", "incomplete", "unknown"):
            raise ValueError("派生指标必须保留完成状态")


def validate_indicator_fingerprint(
    payload: dict[str, Any],
    point_fingerprint: Callable[[], str],
) -> None:
    """校验原生数值协议或显式派生绑定，不读取时钟、来源或存储。"""
    identity = payload["identity"]
    timeframe = identity.get("timeframe")
    context = payload.get("periodContext")
    expected = payload.get("inputFingerprint")
    if timeframe in ("1m", "1d"):
        if context is not None:
            raise ValueError("原生指标请求不能携带派生上下文")
        if point_fingerprint() != expected:
            raise ValueError("inputFingerprint 与规范化 points 不一致")
        return
    if timeframe not in PERIOD_TIMEFRAMES:
        raise ValueError("指标周期不支持")
    if set(identity) != {"symbol", "assetType", "timeframe", "adjustment"}:
        raise ValueError("派生指标 identity 字段无效")
    if (
        not isinstance(identity["symbol"], str)
        or not identity["symbol"].strip()
        or identity["assetType"] not in ("STOCK", "ETF")
        or identity["adjustment"] not in ("none", "qfq", "hfq")
    ):
        raise ValueError("派生指标只支持有效股票/ETF 口径")
    if (
        not isinstance(context, dict)
        or set(context) != {"protocol", "aggregationFingerprint", "pointFingerprint"}
        or context["protocol"] != PERIOD_PROTOCOL
    ):
        raise ValueError("派生指标缺少当前 periodContext")
    for value in (context["aggregationFingerprint"], context["pointFingerprint"], expected):
        if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
            raise ValueError("派生指标指纹必须为 SHA-256")
    _validate_period_points(payload["points"])
    requests = payload.get("requests")
    if not isinstance(requests, list) or not requests:
        raise ValueError("派生指标必须包含计算请求")
    seen: set[str] = set()
    for request in requests:
        if (
            not isinstance(request, dict)
            or request.get("name") not in ("MA", "MACD", "RSI")
            or not isinstance(request.get("parameters"), dict)
        ):
            raise ValueError("派生指标请求字段无效")
        for value in request["parameters"].values():
            if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 200:
                raise ValueError("派生指标参数必须为 2 到 200 之间的整数")
        key = json.dumps([request["name"], request["parameters"]], sort_keys=True, separators=(",", ":"))
        if key in seen:
            raise ValueError("指标请求不得重复")
        seen.add(key)
    if point_fingerprint() != context["pointFingerprint"]:
        raise ValueError("pointFingerprint 与实际计算点不一致")
    encoded = json.dumps(
        [PERIOD_PROTOCOL, context["aggregationFingerprint"], context["pointFingerprint"]],
        separators=(",", ":"),
    )
    binding = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if binding != expected:
        raise ValueError("inputFingerprint 与聚合及计算点绑定不一致")
