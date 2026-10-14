"""保留不确定性的逐行研究标准化；复用读取器和十进制/日期原语。"""

from collections import Counter
from decimal import Decimal
from data_provider.hithink_fund_dividends import _date_ms, _per_unit_cash
from data_provider.hithink_fund_dividend_semantics import is_empty_dividend_row
from data_provider.hithink_dividend_reconciliation import reconciliation_reference

REVISION = "hithink-cash-records-v2"
STATUSES = {"实施": "implemented", "预案": "preliminary", "取消": "cancelled",
            "撤销": "cancelled", "终止": "cancelled"}
DATE_FIELDS = {"publish_date_ms": "announcementDate", "registration_date_ms": "recordDate",
               "ex_dividend_date_ms": "effectiveDate", "payment_date_ms": "paymentDate"}


def _observation(item, index, request):
    row = {"rowIndex": index, "sourceStatus": None, "normalizedStatus": "unknown",
           "sourceAmount": None, "amountPerFundUnit": None, "currency": request["currency"],
           "identityBasis": "request-bound", "currencyBasis": "explicit-research-scope",
           "issues": [], "disposition": "excluded", **{field: None for field in DATE_FIELDS.values()}}
    if not isinstance(item, dict):
        row["issues"].append("INVALID_RECORD_SHAPE")
        return row
    if is_empty_dividend_row(item):
        row.update(disposition="placeholder", issues=["EMPTY_PLACEHOLDER"])
        return row
    status = item.get("progress")
    if isinstance(status, str) and status.strip():
        row["sourceStatus"] = status
        row["normalizedStatus"] = STATUSES.get(status.strip(), "unknown")
    else:
        row["issues"].append("MISSING_DIVIDEND_STATUS")
    if row["normalizedStatus"] == "unknown":
        row["issues"].append("UNKNOWN_DIVIDEND_STATUS")
    for source, field in DATE_FIELDS.items():
        try:
            row[field] = _date_ms(item.get(source), source)
        except ValueError:
            row["issues"].append("INVALID_" + field.upper())
        if row[field] is None:
            row["issues"].append("MISSING_" + field.upper())
    try:
        row["amountPerFundUnit"] = _per_unit_cash(item.get("per_ten_cash_before_tax"))
        # 十进制规范形式；不经float、保留每十份的原业务值。
        amount = format(Decimal(str(item["per_ten_cash_before_tax"])), "f")
        row["sourceAmount"] = amount.rstrip("0").rstrip(".") if "." in amount else amount
    except ValueError:
        row["issues"].append("INVALID_CASH_AMOUNT")
    dates = [row[field] for field in DATE_FIELDS.values()]
    if all(dates) and (dates[0] > dates[1] or dates[1] >= dates[2] or dates[3] < dates[2]):
        row["issues"].append("CONFLICTING_DATES")
    if dates[0] and dates[0] > request["observedDate"]:
        row["issues"].append("ANNOUNCEMENT_AFTER_OBSERVATION")
    if row["effectiveDate"] and not request["start"] <= row["effectiveDate"] <= request["end"]:
        row["disposition"] = "outside-window"
    elif row["normalizedStatus"] in {"preliminary", "cancelled"}:
        row["issues"].append("NON_IMPLEMENTED_PLAN")
    elif set(row["issues"]) <= {"UNKNOWN_DIVIDEND_STATUS"}:
        row["disposition"] = "usable"
    return row


def normalize_research_records(raw, request):
    observations = [_observation(item, index, request) for index, item in enumerate(raw["items"])]
    grouped = {}
    for row in observations:
        if row["disposition"] not in {"outside-window", "placeholder"} and row["effectiveDate"]:
            grouped.setdefault(row["effectiveDate"], []).append(row)
    for rows in grouped.values():
        signatures = {tuple(row[field] for field in ("sourceStatus", "sourceAmount", *DATE_FIELDS.values()))
                      for row in rows}
        if len(signatures) > 1:
            for row in rows:
                row["disposition"] = "excluded"
                row["issues"].append("CONFLICTING_EVENT")
        else:
            for row in rows[1:]:
                row["disposition"] = "duplicate"
                row["issues"].append("DUPLICATE_EVENT")
    records = []
    for row in observations:
        if row["disposition"] != "usable":
            continue
        reference = reconciliation_reference(request["symbol"], row)
        basis = "source-defined" if row["normalizedStatus"] == "implemented" else "unverified"
        if reference:
            basis = "event-reconciled"
        records.append({"symbol": request["symbol"], **{field: row[field] for field in DATE_FIELDS.values()},
                        "amountPerFundUnit": row["amountPerFundUnit"], "currency": row["currency"],
                        "planStatus": "implementation-plan-announced" if basis == "source-defined" else "unknown",
                        "statusBasis": basis, "sourcePlanStatus": row["sourceStatus"],
                        "sourceStatus": row["sourceStatus"], "normalizedStatus": row["normalizedStatus"],
                        "sourceSemanticsRevision": REVISION, "issues": list(row["issues"]),
                        "sourceAmount": {"value": row["sourceAmount"], "unit": "per-ten-fund-units"},
                        **({"reconciliationReference": reference} if reference else {})})
    records.sort(key=lambda row: row["effectiveDate"])
    counts = Counter(row["disposition"] for row in observations)
    return {"records": records, "assessment": {
        "version": "cash-record-assessment-v1", "responseComplete": True,
        "transportCountVerified": raw.get("transportCountVerified", False),
        "historicalCoverageVerified": False, "rawItemCount": raw.get("rawItemCount", len(raw["items"])),
        "counts": {name: counts[name] for name in ("usable", "excluded", "duplicate", "outside-window", "placeholder")},
        "observations": observations, "rawBusinessResponse": raw["rawBusinessResponse"],
    }}
