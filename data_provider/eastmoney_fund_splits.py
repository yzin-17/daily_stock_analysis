"""东财基金拆分观测；源折算日期不等同于已证明的执行生效时间。"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

import pandas as pd


def normalize_eastmoney_fund_splits(
    frame: pd.DataFrame, symbol: str, *, start: str, end: str,
    observed_at: datetime, provider_revision: str,
) -> dict[str, Any]:
    """按源日期筛选观测，未知日内生效阶段不发布 canonical SPLIT。"""
    if not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbol):
        raise ValueError("基金拆分代码必须包含明确交易所")
    lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    if lower > upper or lower.isoformat() != start or upper.isoformat() != end:
        raise ValueError("基金拆分窗口无效")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("基金拆分观测时间必须带时区")
    if not provider_revision.strip():
        raise ValueError("基金拆分来源修订不能为空")
    required = {"基金代码", "拆分折算日", "拆分类型", "拆分折算"}
    if not required.issubset(frame.columns):
        raise ValueError("基金拆分响应字段不完整")
    fetched = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    observations: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        code = row["基金代码"]
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("基金拆分响应代码格式无效")
        if code != symbol.split(".")[0]:
            continue
        raw_date = row["拆分折算日"]
        if isinstance(raw_date, datetime):
            raise ValueError("基金拆分源日期不能隐式截断时间")
        converted = raw_date.isoformat() if isinstance(raw_date, date) else raw_date
        if not isinstance(converted, str) or date.fromisoformat(converted).isoformat() != converted:
            raise ValueError("基金拆分源日期无效")
        if not start <= converted <= end:
            continue
        kind = row["拆分类型"]
        if kind not in {"份额分拆", "份额折算"}:
            raise ValueError("基金拆分类型未识别")
        try:
            ratio = Decimal(str(row["拆分折算"]))
        except InvalidOperation as exc:
            raise ValueError("基金拆分比例无效") from exc
        if not ratio.is_finite() or ratio <= 0:
            raise ValueError("基金拆分比例必须为有限正数")
        value = format(ratio, "f")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        observation = {
            "symbol": symbol, "sourceConversionDate": converted, "sourceType": kind,
            "sourceRatioPerUnit": value, "effectiveDate": None, "effectivePhase": "unknown",
            "announcementDate": None, "observedAt": fetched,
            "endpoint": "fund_cf_em", "upstreamSource": "eastmoney",
            "providerRevision": provider_revision,
        }
        previous = observations.get(converted)
        if previous is not None and previous != observation:
            raise ValueError("同一折算日存在冲突拆分记录")
        observations[converted] = observation
    return {
        "facts": [], "observations": [observations[key] for key in sorted(observations)],
        "coverage": {"start": start, "end": end, "complete": False},
        "unavailableReason": "split_effective_phase_unverified",
    }
