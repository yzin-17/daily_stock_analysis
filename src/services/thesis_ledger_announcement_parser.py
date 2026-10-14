"""从正文明确年月日读取停复牌事实；不把预计安排或历史引用当新事实。"""

from datetime import date
from io import BytesIO
import re

PARSER_REVISION = "cninfo-explicit-status-risk-v3"


def parse_status_text(text, published_date):
    text = re.sub(r"\s+", "", text)
    prefix = r"(?:(?:公司股票|公司证券|股票交易|股票|证券)(?:[（(][^）)]*[）)])?(?:将于|将自|于|自)|并于|并自)"
    pattern = prefix + r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:[（(][^）)]*[）)])?(?:(?:上午)?开市起?|起)?(?:开始)?(停牌|复牌)"
    facts = {}
    for match in re.finditer(pattern, text):
        context = re.split(r"[。；]", text[:match.start()])[-1][-45:]
        if any(word in context for word in ("预计", "最晚", "如果", "若", "如未", "拟于")):
            continue
        effective = date(*(int(match[i]) for i in (1, 2, 3))).isoformat()
        if effective < published_date:
            continue
        state = "suspended" if match[4] == "停牌" else "trading"
        if effective in facts and facts[effective]["state"] != state:
            raise ValueError("同一日存在矛盾停复牌事实")
        facts[effective] = {"date": effective, "state": state, "text": match[0]}
    return sorted(facts.values(), key=lambda item: item["date"])


def parse_announcement(record, raw):
    from pypdf import PdfReader
    record["parserRevision"] = PARSER_REVISION
    try:
        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) > 30:
            raise ValueError("原件超过首版解析页数")
        text = "".join(page.extract_text() or "" for page in reader.pages)
        if record["symbol"].split(".")[0] not in re.sub(r"\s+", "", text):
            raise ValueError("公告原件未找到对应证券代码")
        record["days"] = parse_status_text(text, record["publishedDate"])
        from src.services.thesis_ledger_announcement_risk_parser import parse_risk_text
        record["riskDays"] = parse_risk_text(text, record["publishedDate"])
        if record["days"] or record["riskDays"]:
            record["parseState"] = "parsed"
        else:
            record["parseState"] = "unparsed"
            record["detail"] = "未识别到明确的停复牌或风险警示生效日期"
    except Exception as exc:
        record["parseState"] = "failed"
        record["detail"] = f"原文解析失败：{exc}"
