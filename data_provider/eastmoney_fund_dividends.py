"""东财基金分红结构化观测；不从事件行推断历史覆盖或公告可见性。"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


def _date(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and value.strip() in {"", "--", "-"}) or pd.isna(value):
        if optional:
            return None
        raise ValueError(f"基金分红缺少 {field}")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(ZoneInfo("Asia/Shanghai"))
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"基金分红 {field} 日期无效") from exc


def normalize_eastmoney_fund_dividends(
    frame: pd.DataFrame,
    symbol: str,
    *,
    start: str,
    end: str,
    observed_at: datetime,
    provider_revision: str,
) -> dict[str, Any]:
    """标准化 fund_fh_em 的元/份金额；未知公告时间保持未知。

    观测记录保留三种源日期。canonical fact 以除息日生效、当前抓取时间
    availableAt，不生成 strategyVisibility。分页/来源准入由调用层另证。
    """
    if not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbol):
        raise ValueError("基金代码必须包含明确交易所")
    lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    if lower.isoformat() != start or upper.isoformat() != end:
        raise ValueError("基金分红窗口日期必须使用 YYYY-MM-DD")
    if lower > upper:
        raise ValueError("基金分红窗口起止日期颠倒")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("基金分红观测时间必须带时区")
    if not provider_revision.strip():
        raise ValueError("基金分红来源修订不能为空")
    required = {"基金代码", "权益登记日", "除息日期", "分红", "分红发放日"}
    if not required.issubset(frame.columns):
        raise ValueError("基金分红响应字段不完整")
    code = symbol.split(".")[0]
    fetched = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    observations: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        # 浮点/数值代码可能已丢失前导零，不进行猜测性补零。
        if not isinstance(row["基金代码"], str) or not re.fullmatch(r"[0-9]{6}", row["基金代码"]):
            raise ValueError("基金分红响应代码格式无效")
        if row["基金代码"] != code:
            continue
        effective = _date(row["除息日期"], "除息日期")
        if not start <= effective <= end:
            continue
        registered = _date(row["权益登记日"], "权益登记日", optional=True)
        paid = _date(row["分红发放日"], "分红发放日", optional=True)
        if (registered and registered > effective) or (paid and paid < effective):
            raise ValueError("基金分红日期顺序冲突")
        try:
            amount = Decimal(str(row["分红"]))
        except InvalidOperation as exc:
            raise ValueError("基金分红金额无效") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("基金分红金额必须为有限正数")
        cash = format(amount, "f")
        if "." in cash:
            cash = cash.rstrip("0").rstrip(".")
        observation = {
            "effectiveDate": effective, "recordDate": registered, "paymentDate": paid,
            "cashAmount": cash, "currency": "CNY", "cashUnit": "per-fund-unit",
            "announcementDate": None, "observedAt": fetched,
            "endpoint": "fund_fh_em", "upstreamSource": "eastmoney",
        }
        previous = observations.get(effective)
        if previous is not None and previous != observation:
            raise ValueError("同一除息日存在冲突分红记录")
        observations[effective] = observation
    ordered = [observations[key] for key in sorted(observations)]
    facts = [{
        "symbol": symbol, "market": "CN", "instrumentType": "ETF", "type": "CASH_DIVIDEND",
        "effectiveDate": item["effectiveDate"], "cashAmount": item["cashAmount"], "currency": "CNY",
        "occurredAt": f'{item["effectiveDate"]}T00:00:00+08:00', "availableAt": fetched,
        "provider": "akshare", "providerRevision": provider_revision,
        **({"recordDate": item["recordDate"]} if item["recordDate"] is not None else {}),
        **({"paymentDate": item["paymentDate"]} if item["paymentDate"] is not None else {}),
    } for item in ordered]
    return {
        "facts": facts, "observations": ordered,
        "coverage": {"start": start, "end": end, "complete": False},
    }
