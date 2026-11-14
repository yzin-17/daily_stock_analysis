"""已审阅美国现金证券规则；有限适用期，独立于富途快照和历史身份资料。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo
from copy import deepcopy


SOURCES = [
    {'id': 'reg-nms-original', 'url': 'https://www.sec.gov/files/rules/final/34-51808.pdf'},
    {'id': 'reg-nms-2026-relief', 'url': 'https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf',
     'sha256': 'f11d3b90cabe2d2432eef3b2f56199ae8c411d6e0c6a12ffd08d1fd9053fc7e9'},
    {'id': 'sec-section31-2026', 'url': 'https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2',
     'sha256': '7843dd56c22390c5ca54457232fd3b572cffda370a1127b7b16fd3046fad6eab'},
    {'id': 'finra-taf-pause-2026', 'url': 'https://www.sec.gov/files/rules/sro/finra/2026/34-106409.pdf',
     'sha256': 'b45080f51ebf9208784588bed02da224ae76f64675deec6cc1ad005ad9393aae'},
    {'id': 'finra-taf-pause-text', 'url': 'https://www.sec.gov/files/rules/sro/finra/2026/34-106409-ex5.pdf',
     'sha256': '6dc60f294ecd26f46c06784bcdab9fe09c709753a628b97d13970d0291b0be59'},
    {'id': 'sec-t1', 'url': 'https://www.sec.gov/newsroom/press-releases/2024-62',
     'sha256': 'b51a3d115ebdc0aa3a400df3169c2b611eceb0221897fa3d32e72cb15af372a5'},
]
VERIFIED_THROUGH = '2026-11-14'
BASE_RULES = {
    'version': 1, 'ruleSetId': 'us-listed-cash-equity-2026-11-14-v1',
    'market': 'US', 'currency': 'USD', 'assetTypes': ['STOCK', 'ETF'],
    'exchanges': ['US_NYSE', 'US_NASDAQ', 'US_AMEX'],
    'effectiveFrom': '2026-10-01', 'effectiveUntil': '2026-12-31',
    'reviewedAt': '2026-11-14T06:50:00Z', 'provider': 'official-us-rules',
    'priceIncrements': {'atOrAboveOne': '0.01', 'belowOne': '0.0001'},
    'positionSettlementDays': 1, 'sellCashSettlementDays': 1,
    'section31SellRate': '0.0000206', 'tafSellRatePerShare': '0', 'tafSellMaximum': '0',
    'sources': SOURCES,
}


def current_us_cash_rules(day, *, observed_at, context=None):
    """只返回当前已核查日期；未来日期须重新完成来源核查，不能外推覆盖。"""
    if not isinstance(day, str) or not VERIFIED_THROUGH <= day <= '2026-12-31':
        return None
    try:
        normalized = date.fromisoformat(day).isoformat()
    except ValueError:
        return None
    if normalized != day:
        return None
    observed = datetime.fromisoformat(observed_at)
    if observed.tzinfo is None or observed.astimezone(ZoneInfo('America/New_York')).date().isoformat() != day:
        return None
    reviewed_at = BASE_RULES['reviewedAt']
    if day > VERIFIED_THROUGH:
        from src.services.us_live_rules_refresh import checked_through
        try:
            reviewed_at = checked_through(day, context)
        except Exception:
            # 规则监测存储或来源不可用仅阻断执行资料，不覆盖已经取得的正常报价。
            return None
    if reviewed_at is None or datetime.fromisoformat(reviewed_at) > observed:
        return None
    return {**deepcopy(BASE_RULES), 'verifiedThrough': day, 'reviewedAt': reviewed_at, 'observedAt': observed_at}
