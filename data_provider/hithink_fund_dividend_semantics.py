"""HiThink分红字段与显式研究解释；不证明历史覆盖或实际支付。"""

from decimal import Decimal
from typing import Any


PLAN_ANNOUNCED_SEMANTICS = "hithink-plan-announced-v1"
RESEARCH_RECORDS_SEMANTICS = "hithink-cash-records-v2"
PLAN_ANNOUNCED_LABEL = "实施方案已公布"
DATE_FIELDS = {
    "publish_date_ms": "announcementDate",
    "registration_date_ms": "recordDate",
    "ex_dividend_date_ms": "effectiveDate",
    "payment_date_ms": "paymentDate",
    "reinvestment_date_ms": "reinvestmentDate",
    "profit_base_date_ms": "profitBaseDate",
    "in_dividend_date_ms": "inDividendDate",
}
BUSINESS_FIELDS = frozenset(DATE_FIELDS) | {
    "progress", "per_ten_cash_before_tax", "per_ten_cash_after_tax",
}


def validate_research_semantics(value: str | None) -> None:
    if value not in {None, PLAN_ANNOUNCED_SEMANTICS, RESEARCH_RECORDS_SEMANTICS}:
        raise ValueError("HiThink 分红研究语义版本无效")


def is_empty_dividend_row(item: dict[str, Any]) -> bool:
    """只识别已取得的齐全全空形状，未知非空字段不能被忽略。"""
    return BUSINESS_FIELDS.issubset(item) and all(value is None for value in item.values())


def normalize_dividend_progress(value: Any, research_semantics: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("HiThink 分红进度缺失")
    progress = value.strip()
    if progress in {"实施", "预案"}:
        return progress
    if progress == "2" and research_semantics == PLAN_ANNOUNCED_SEMANTICS:
        return PLAN_ANNOUNCED_LABEL
    raise ValueError("HiThink 分红进度口径未经核验")


def research_observation_metadata(item: dict[str, Any], semantics: str) -> dict[str, Any]:
    """金额须先由normalizer校验；原值不作为支付完成证明。"""
    metadata = {
        "rawPlanProgress": item["progress"], "progressSemantics": semantics,
        "progressSemanticsBasis": "documented-label", "cashOriginalUnit": "per-ten-fund-units",
    }
    if item["progress"].strip() == "2":
        metadata["progressSemanticsBasis"] = "research-assumption"
    for source, output in (
        ("per_ten_cash_before_tax", "cashBeforeTaxPerTen"),
        ("per_ten_cash_after_tax", "cashAfterTaxPerTen"),
    ):
        value = item.get(source)
        metadata[output] = format(Decimal(str(value)).normalize(), "f") if value is not None else None
    return metadata
