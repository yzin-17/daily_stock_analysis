"""HiThink 基金分红字段标准化；历史覆盖与来源准入由调用方证明。"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
import re
from typing import Any
from zoneinfo import ZoneInfo

from data_provider.hithink_fund_dividend_semantics import (
    DATE_FIELDS, PLAN_ANNOUNCED_LABEL, is_empty_dividend_row,
    normalize_dividend_progress, research_observation_metadata, validate_research_semantics,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _date_ms(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"HiThink 分红 {field} 必须为毫秒时间戳")
    try:
        local = (EPOCH + timedelta(milliseconds=value)).astimezone(SHANGHAI)
    except OverflowError as exc:
        raise ValueError(f"HiThink 分红 {field} 时间戳无效") from exc
    if not 1990 <= local.year <= 2100:
        raise ValueError(f"HiThink 分红 {field} 日期超出支持范围")
    return local.date().isoformat()


def _per_unit_cash(value: Any, *, allow_zero: bool = False) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("HiThink 分红税前金额无效")
    try:
        original = Decimal(str(value))
        if len(str(value)) > 256 or abs(original.as_tuple().exponent) > 256:
            raise ValueError("HiThink 分红金额超出十进制存储预算")
        with localcontext() as context:
            context.prec = max(28, len(str(value)) + 2)
            amount = original / Decimal(10)
    except (DecimalException, ValueError, TypeError) as exc:
        raise ValueError("HiThink 分红税前金额无效") from exc
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError("HiThink 分红金额必须为有限非负数，税前金额必须大于零")
    text = format(amount, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize_hithink_fund_dividends(
    items: list[dict[str, Any]],
    symbol: str,
    *,
    instrument_type: str,
    currency: str,
    start: str,
    end: str,
    observed_at: datetime,
    provider_revision: str,
    research_semantics: str | None = None,
) -> dict[str, Any]:
    """标准化官方 item[]；币种/身份已核验才由调用方传入。"""
    validate_research_semantics(research_semantics)
    if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|OF)", symbol):
        raise ValueError("HiThink 分红需要完整基金代码")
    if instrument_type not in {"ETF", "NAV_FUND"} or (instrument_type == "ETF" and symbol.endswith(".OF")):
        raise ValueError("HiThink 分红标的类型不一致")
    if currency not in {"CNY", "HKD", "USD"}:
        raise ValueError("HiThink 分红需要已核验币种")
    try:
        lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise ValueError("HiThink 分红窗口日期无效") from exc
    if lower.isoformat() != start or upper.isoformat() != end or lower > upper:
        raise ValueError("HiThink 分红窗口日期无效")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("HiThink 分红观测时间必须带时区")
    if not isinstance(provider_revision, str) or not provider_revision.strip():
        raise ValueError("HiThink 分红来源修订不能为空")
    if not isinstance(items, list):
        raise ValueError("HiThink 分红 item 必须为数组")

    fetched = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    observations: list[dict[str, Any]] = []
    facts: dict[str, dict[str, Any]] = {}
    resolved_observations: dict[str, dict[str, Any]] = {}
    ignored_empty_rows = 0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("HiThink 分红记录格式无效")
        if research_semantics is not None and is_empty_dividend_row(item):
            ignored_empty_rows += 1
            continue
        dates = {output: _date_ms(item.get(source), source) for source, output in DATE_FIELDS.items()}
        progress = normalize_dividend_progress(item.get("progress"), research_semantics)
        effective = dates["effectiveDate"]
        if progress in {"实施", PLAN_ANNOUNCED_LABEL} and effective is None:
            if progress == PLAN_ANNOUNCED_LABEL:
                raise ValueError("HiThink 已公布分红方案缺少除息日")
            raise ValueError("HiThink 已实施分红缺少除息日")
        if progress == PLAN_ANNOUNCED_LABEL and dates["announcementDate"] is None:
            raise ValueError("HiThink 已公布分红方案缺少公告日")
        if (progress == PLAN_ANNOUNCED_LABEL
                and dates["announcementDate"] > observed_at.astimezone(SHANGHAI).date().isoformat()):
            raise ValueError("HiThink 分红方案公告日晚于观察时点")
        if effective is not None and not start <= effective <= end:
            continue
        cash = None
        if item.get("per_ten_cash_before_tax") is not None:
            cash = _per_unit_cash(item["per_ten_cash_before_tax"])
        after_tax_cash = None
        if item.get("per_ten_cash_after_tax") is not None:
            after_tax_cash = _per_unit_cash(item["per_ten_cash_after_tax"], allow_zero=True)
        if progress in {"实施", PLAN_ANNOUNCED_LABEL} and cash is None:
            if progress == PLAN_ANNOUNCED_LABEL:
                raise ValueError("HiThink 已公布分红方案缺少税前金额")
            raise ValueError("HiThink 已实施分红缺少税前金额")
        if effective is not None and (
            (dates["recordDate"] and dates["recordDate"] > effective)
            or (dates["paymentDate"] and dates["paymentDate"] < effective)
            or (dates["announcementDate"] and dates["announcementDate"] > effective)
        ):
            raise ValueError("HiThink 分红日期顺序冲突")
        observation = {
            **dates, "planProgress": progress, "cashAmount": cash,
            "cashAfterTaxAmount": after_tax_cash,
            "cashUnit": "per-fund-unit", "currency": currency,
            "observedAt": fetched, "endpoint": "/api/fund/corporate-actions/dividends",
            "upstreamSource": "hithink",
        }
        if research_semantics is not None:
            observation.update(research_observation_metadata(item, research_semantics))
        if observation not in observations:
            observations.append(observation)
        if progress not in {"实施", PLAN_ANNOUNCED_LABEL}:
            continue
        if effective in resolved_observations and resolved_observations[effective] != observation:
            raise ValueError("HiThink 同一除息日存在冲突分红记录")
        resolved_observations[effective] = observation
        if progress != "实施":
            continue
        fact = {
            "symbol": symbol, "market": "CN", "instrumentType": instrument_type,
            "type": "CASH_DIVIDEND", "effectiveDate": effective,
            "cashAmount": cash, "currency": currency,
            "occurredAt": f"{effective}T00:00:00+08:00", "availableAt": fetched,
            "provider": "hithink", "providerRevision": provider_revision,
        }
        for field in ("recordDate", "paymentDate"):
            if dates[field] is not None:
                fact[field] = dates[field]
        if effective in facts and facts[effective] != fact:
            raise ValueError("HiThink 同一除息日存在冲突分红记录")
        facts[effective] = fact

    if any(item["effectiveDate"] in resolved_observations
           and item["planProgress"] != resolved_observations[item["effectiveDate"]]["planProgress"]
           for item in observations):
        raise ValueError("HiThink 同一除息日进度冲突，需要修订证据")
    observations.sort(key=lambda item: (item["effectiveDate"] or "", item["announcementDate"] or "", item["planProgress"]))
    result = {
        "facts": [facts[key] for key in sorted(facts)], "observations": observations,
        "coverage": {"start": start, "end": end, "complete": False},
    }
    if research_semantics is not None:
        result.update({"researchSemantics": research_semantics, "ignoredEmptyRows": ignored_empty_rows})
    return result
