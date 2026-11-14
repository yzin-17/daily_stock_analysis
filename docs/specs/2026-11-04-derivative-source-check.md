# 衍生品公开来源采证与路由契约

对应[任务](../../../thesis-ledger-infra/docs/archive/tasks/2026-11-04-derivative-source-check.md)，承接[主仓规格](../../../thesis-ledger/docs/specs/2026-11-04-astock-derivative-reference.md)。

扩展现有公开来源采证入口，固定参考代码中的期货、ETF 期权与可转债 URL 和参数，默认只显示计划，显式执行才读取一次。必要月份／合约关联必须核对父回执摘要和身份；不能接受任意 URL、代码、请求头或凭据。失败、重定向和限频原样记录，不自动重试，不执行外部 Skill，不变更 Provider、准入或业务库。费用 0 USD，无模型或通知请求。

合格产品的路由计划与 DSA 资料合同使用相同资产身份；普通 Bar Route 保持原范围。只通过现有 Server 条件策略 API 追加精确目标，保护原 67 条路由与 Provider／准入。部署继续使用 `sync-code.sh` 或预检要求的 `update.sh`，采证不是部署入口；目标真实产品验证由主仓和 DSA 持有。
