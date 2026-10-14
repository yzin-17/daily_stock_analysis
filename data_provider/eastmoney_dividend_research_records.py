"""单基金分红表：原文本/每十份单位/缺字段保留，不签发状态或覆盖。"""

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import re
from bs4 import BeautifulSoup

REVISION = "eastmoney-cash-records-v2"
HEADERS = ["年份", "权益登记日", "除息日", "每10份分红", "分红发放日"]

# 复用已核对公告的事实数据；本来源仅匹配金额/三日期及缺状态，不填公告日。
from data_provider.verified_cash_dividend_events_v1 import VERIFIED_CASH_DIVIDEND_EVENTS_V1
EVENTS = tuple((event[0], event[1], *event[3:]) for event in VERIFIED_CASH_DIVIDEND_EVENTS_V1)


def parse_research_page(body, symbol):
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text() if soup.title else ""
    if not re.search(r"\(" + re.escape(symbol.split(".")[0]) + r"\)", title):
        raise ValueError("页面基金代码回显不匹配")
    tables = [table for table in soup.find_all("table")
              if [cell.get_text(strip=True) for cell in table.find_all("th")] == HEADERS]
    if len(tables) != 1:
        raise ValueError("每10份分红表缺失、重复或单位无法确定")
    rows = [[cell.get_text(strip=True) for cell in row.find_all("td")]
            for row in tables[0].find_all("tr") if row.find_all("td")]
    if len(rows) > 5000:
        raise ValueError("基金分红记录超过预算")
    return rows


def _date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("现金日期无效")
    return value


def _row(cells, index, request):
    row = {"rowIndex": index, "sourceStatus": None, "normalizedStatus": "unknown",
           "sourceAmount": None, "amountPerFundUnit": None, "currency": request["currency"],
           "identityBasis": "supplier-code-echo", "currencyBasis": "explicit-research-scope",
           "announcementDate": None, "recordDate": None, "effectiveDate": None,
           "paymentDate": None, "issues": ["MISSING_DIVIDEND_STATUS", "UNKNOWN_DIVIDEND_STATUS",
                                          "MISSING_ANNOUNCEMENT_DATE"], "disposition": "excluded"}
    if any("暂无分红信息" in cell for cell in cells):
        row.update(disposition="placeholder", issues=["SOURCE_NO_DIVIDEND_INFORMATION"])
        return row
    if len(cells) != 5:
        row["issues"].append("INVALID_RECORD_SHAPE")
        return row
    row["sourceAmount"] = cells[3]
    for index, name in ((1, "recordDate"), (2, "effectiveDate"), (4, "paymentDate")):
        try:
            row[name] = _date(cells[index])
        except (ValueError, TypeError):
            row["issues"].append("INVALID_" + name.upper())
    match = re.fullmatch(r"每10份派现金([0-9]{1,128}(?:\.[0-9]{1,128})?)元", cells[3])
    try:
        if not match:
            raise ValueError("原金额文本/单位不符")
        amount = Decimal(match[1])
        if not amount.is_finite() or amount <= 0:
            raise ValueError("非正金额")
        with localcontext() as context:
            context.prec = max(28, len(amount.as_tuple().digits) + 2)
            row["amountPerFundUnit"] = format(amount / 10, "f")
    except (InvalidOperation, ValueError):
        row["issues"].append("INVALID_CASH_AMOUNT")
    dates = [row[name] for name in ("recordDate", "effectiveDate", "paymentDate")]
    if all(dates) and (dates[0] >= dates[1] or dates[2] < dates[1]):
        row["issues"].append("CONFLICTING_DATES")
    if row["effectiveDate"] and not request["start"] <= row["effectiveDate"] <= request["end"]:
        row["disposition"] = "outside-window"
    elif len(row["issues"]) == 3:
        row["disposition"] = "usable"
    return row


def normalize_eastmoney_research_records(raw, request):
    cells = parse_research_page(raw["rawBusinessResponse"], request["symbol"])
    observations = [_row(row, index, request) for index, row in enumerate(cells)]
    groups = {}
    for row in observations:
        if row["disposition"] not in {"outside-window", "placeholder"} and row["effectiveDate"]:
            groups.setdefault(row["effectiveDate"], []).append(row)
    for rows in groups.values():
        signatures = {tuple(row[name] for name in ("sourceAmount", "recordDate", "effectiveDate", "paymentDate")) for row in rows}
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
        original = re.fullmatch(r"每10份派现金(.+)元", row["sourceAmount"])[1]
        amount = Decimal(original)
        reference = next((event[-1] for event in EVENTS
                          if event[:1] == (request["symbol"],) and Decimal(event[1]) == amount
                          and event[2:5] == tuple(row[name] for name in ("recordDate", "effectiveDate", "paymentDate"))), None)
        records.append({"symbol": request["symbol"], "announcementDate": None,
                        **{name: row[name] for name in ("recordDate", "effectiveDate", "paymentDate")},
                        "amountPerFundUnit": row["amountPerFundUnit"], "currency": request["currency"],
                        "planStatus": "unknown", "statusBasis": "event-reconciled" if reference else "unverified",
                        "sourcePlanStatus": None, "sourceStatus": None, "normalizedStatus": "unknown",
                        "sourceSemanticsRevision": REVISION, "issues": list(row["issues"]),
                        "sourceAmount": {"value": format(amount, "f"), "unit": "per-ten-fund-units"},
                        **({"reconciliationReference": reference} if reference else {})})
    records.sort(key=lambda row: row["effectiveDate"])
    counts = Counter(row["disposition"] for row in observations)
    return {"records": records, "assessment": {"version": "cash-record-assessment-v1",
            "responseComplete": True, "transportCountVerified": False, "historicalCoverageVerified": False,
            "rawItemCount": len(cells), "counts": {name: counts[name] for name in
            ("usable", "excluded", "duplicate", "outside-window", "placeholder")},
            "observations": observations, "rawBusinessResponse": raw["rawBusinessResponse"]}}
