"""只读取明确日期的风险变更与明确比例，不延伸未知历史区间。"""
from datetime import date
from decimal import Decimal
import re


def parse_risk_text(text, published_date):
    compact = re.sub(r"\s+", "", text)
    pattern = (r"(?:公司(?:股票(?:交易)?)?|股票(?:交易)?)?(?:将于|将自|自|于)"
               r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:[（(][^）)]*[）)])?(?:开市起?|起)?"
               r"(?:被)?(撤销|实施)(退市|其他)风险警示")
    facts = {}
    for match in re.finditer(pattern, compact):
        context = re.split(r"[。；]", compact[:match.start()])[-1][-60:]
        if any(word in context for word in ("预计", "拟", "申请", "若", "如果")):
            continue
        effective = date(*(int(match[i]) for i in (1, 2, 3))).isoformat()
        if effective < published_date:
            continue
        field = "delisting" if match[5] == "退市" else "other"
        state = "none" if match[4] == "撤销" else "active"
        row = facts.setdefault(effective, {"date": effective, "state": "unknown",
                                          "riskWarning": {"delisting": "unknown", "other": "unknown"}})
        if row["riskWarning"][field] not in ("unknown", state):
            raise ValueError("同一天存在矛盾风险警示事实")
        row["riskWarning"][field] = state
    if len(facts) == 1:
        limits = re.findall(r"(?:股票交易价格的?日涨跌幅限制|股票交易日涨跌幅限制|股票交易价格日涨跌幅限制)"
                            r"(?:由[“\"]?\d+(?:\.\d+)?%[”\"]?(?:变更|调整)为|(?:调整为|为))"
                            r"[“\"]?(\d+(?:\.\d+)?)%", compact)
        if limits and len(set(limits)) == 1:
            ratio = Decimal(limits[0]) / 100
            if 0 < ratio < 1:
                normalized = format(ratio.normalize(), "f")
                next(iter(facts.values()))["dailyPriceLimit"] = {
                    "reference": "previousRawClose", "maxUpRatio": normalized, "maxDownRatio": normalized}
    return sorted(facts.values(), key=lambda row: row["date"])
