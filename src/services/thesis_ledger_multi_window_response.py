"""把已校验的单窗口 wire 响应组合为保留完整观测的多窗口响应。"""

from copy import deepcopy
from datetime import datetime
from typing import Any

from src.services.thesis_ledger_multi_window_fingerprint import multi_window_content_hash


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("观测时间必须包含时区")
    return parsed


def build_multi_window_response(parent: dict, observations: list[dict]) -> dict:
    """父响应提供已校验的全窗覆盖；子响应保留各自来源事实，不推断全局基准。"""
    if not 2 <= len(observations) <= 1000:
        raise ValueError("多窗口观测数量非法")
    result = deepcopy(parent)
    if "windowObservations" in result:
        raise ValueError("不能递归构造多窗口响应")
    rows: dict[str, dict[str, Any]] = {}
    previous_start = None
    previous_dates = None
    completed = []
    price_fields = ("timestamp", "open", "high", "low", "close", "volume", "amount", "completionStatus")
    basis_fields = ("adjustment", "method", "methodVersion", "anchor", "volumeBasis", "fieldUnits",
                    "dividendMeaning", "dividendEvidenceRef", "conversionAvailable", "conversionEvidenceRef")
    for observation in observations:
        child = observation["response"]
        if "windowObservations" in child or set(observation) != {"response", "startedAt", "completedAt"}:
            raise ValueError("子观测结构非法")
        if _time(observation["startedAt"]) > _time(observation["completedAt"]):
            raise ValueError("观测时间倒置")
        if _time(child["sourcePriceBasis"]["observedAt"]) > _time(observation["completedAt"]):
            raise ValueError("子响应观测晚于采集完成")
        completed.append(observation["completedAt"])
        if any(child[field] != parent[field] for field in ("contractVersion", "symbol", "routeKey", "provenance")):
            raise ValueError("子窗口身份不一致")
        if any(child["sourcePriceBasis"].get(field) != parent["sourcePriceBasis"].get(field) for field in basis_fields):
            raise ValueError("子窗口价格协议不一致")
        start, end = child["coverage"]["requestedStart"], child["coverage"]["requestedEnd"]
        if (start < parent["coverage"]["requestedStart"] or end > parent["coverage"]["requestedEnd"]
                or start > end or previous_start is not None and start <= previous_start):
            raise ValueError("子窗口范围非法")
        dates = {row["timestamp"] for row in child["bars"]}
        if len(dates) != len(child["bars"]) or not dates:
            raise ValueError("子窗口日期为空或重复")
        if previous_dates is not None and not dates.intersection(previous_dates):
            raise ValueError("相邻窗口缺少已观测交集")
        previous_start, previous_dates = start, dates
        for point in child["bars"]:
            existing = rows.get(point["timestamp"])
            if existing and any(existing[field] != point[field] for field in price_fields):
                raise ValueError("子窗口重叠行情冲突")
            if existing is None or _time(point["availableAt"]) > _time(existing["availableAt"]):
                rows[point["timestamp"]] = deepcopy(point)
    if len(rows) != len(parent["bars"]):
        raise ValueError("子窗口并集未覆盖父响应")
    for point in parent["bars"]:
        observed = rows.get(point["timestamp"])
        if observed is None or any(observed[field] != point[field] for field in price_fields):
            raise ValueError("父响应与子窗口行情不一致")
    result["bars"] = [rows[point["timestamp"]] for point in parent["bars"]]
    result["windowObservations"] = deepcopy(observations)
    result["sourcePriceBasis"]["basisScope"] = "request-window"
    result["sourcePriceBasis"]["observedAt"] = max(completed, key=_time)
    result["sourcePriceBasis"]["revision"] = {"origin": "local-observation", "contentHash": "0" * 64}
    fingerprint = multi_window_content_hash(result)
    result["inputFingerprint"] = fingerprint
    result["sourcePriceBasis"]["revision"]["contentHash"] = fingerprint
    return result
