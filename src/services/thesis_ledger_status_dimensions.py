"""状态包可选风险与日涨跌幅维度的验证；不把未知交易日改为正常。"""

from decimal import Decimal, InvalidOperation
from datetime import datetime
import re
from typing import Mapping


def validate_status_documents(documents: list, observed_at: datetime) -> None:
    ids = [item["id"] for item in documents]
    if len(set(ids)) != len(ids):
        raise ValueError("状态来源文档标识重复")
    for item in documents:
        if "sha256" not in item and "observedAt" not in item:
            continue
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError("原件摘要无效")
        value = item.get("observedAt")
        if not isinstance(value, str):
            raise ValueError("原件观察时钟缺失")
        clock = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if clock.tzinfo is None or clock > observed_at:
            raise ValueError("原件观察时钟未到或时区缺失")


def validate_status_dimensions(item: Mapping) -> None:
    from src.services.thesis_ledger_listing_dimensions import validate_listing_dimensions

    validate_listing_dimensions(item)
    risk = item.get("riskWarning")
    limit = item.get("dailyPriceLimit")
    if (item["state"] == "unknown" and risk is None and limit is None
            and item.get("listingPhase") is None and item.get("noDailyPriceLimit") is None):
        raise ValueError("来源包不得用完全未知记录补造事实")
    if risk is not None:
        if not isinstance(risk, dict) or set(risk) != {"delisting", "other"}:
            raise ValueError("风险警示维度无效")
        if any(value not in {"active", "none", "unknown"} for value in risk.values()):
            raise ValueError("风险警示值无效")
        if set(risk.values()) == {"unknown"}:
            raise ValueError("风险维度完全未知，不能声明已知来源事实")
    if limit is not None:
        if not isinstance(limit, dict) or set(limit) != {"reference", "maxUpRatio", "maxDownRatio"}:
            raise ValueError("价格限制维度无效")
        if limit["reference"] != "previousRawClose":
            raise ValueError("价格限制必须使用原始前收盘")
        if any(not isinstance(limit[key], str) for key in ("maxUpRatio", "maxDownRatio")):
            raise ValueError("价格限制比例必须保留十进制字符串")
        try:
            ratios = [Decimal(limit[key]) for key in ("maxUpRatio", "maxDownRatio")]
        except (TypeError, InvalidOperation) as exc:
            raise ValueError("价格限制比例无效") from exc
        if any(not ratio.is_finite() or not 0 < ratio < 1 for ratio in ratios):
            raise ValueError("价格限制比例必须在0与1之间")
