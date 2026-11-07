# 基金收盘比较的公开净值读取

主仓[每日基金估算误差规格](../../../thesis-ledger/docs/specs/2026-11-06-fund-daily-estimate-error.md)限定一次必要读取。专用服务入口为带原合同鉴权的 `GET /api/v3/thesis-ledger/market/fund-nav/public-comparison?symbol=000001.OF`。只在有效策略的第一目标为已准入的 AKShare／东方财富时执行，能力、控制修订与返回校验复用现有净值合同。执行集合只含该主目标，来源失败后返回失败。模拟模式拒绝该入口。

普通净值读取仍可按原配置选择来源。新增离线反例中，主来源给出无效日期，备用来源调用数为零；`tests/test_thesis_ledger_nav_dates.py` 共 13 项通过。本机目标 DSA 使用 infra 的 `./scripts/sync-code.sh dsa` 同步。主仓实际同日样本使用已有缓存；未另发专用入口实源请求。[联合验证记录](../../../thesis-ledger/docs/evidence/2026-11-07-user-workflow-continuity.md)。
