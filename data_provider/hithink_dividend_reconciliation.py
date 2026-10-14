"""已核对的具体事件；绑定核对时的原进度及金额/四日期，不定义进度枚举。"""

# 原文核对及本轮复核见主仓 2026-10-09-history-data-on-demand-bounded-followup.md。
# 引用随标准记录冻结，后续研究不需重复人工核对。
from data_provider.verified_cash_dividend_events_v1 import VERIFIED_CASH_DIVIDEND_EVENTS_V1 as RECONCILED_EVENTS


def reconciliation_reference(symbol, observation):
    # 旧核对所见原值为2；未来原进度变化不能自动沿用旧事件核对范围。
    if not isinstance(observation["sourceStatus"], str) or observation["sourceStatus"].strip() != "2":
        return None
    signature = (symbol, observation["sourceAmount"], observation["announcementDate"],
                 observation["recordDate"], observation["effectiveDate"], observation["paymentDate"])
    return next((event[-1] for event in RECONCILED_EVENTS if event[:-1] == signature), None)
