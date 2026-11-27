# 后台报价来源执行任务

[现行规格](../../specs/2026-11-06-background-quote-source.md)。

2026-11-28 结案。最终核对覆盖 DSA 精确源执行、ThesisLedger 消费、目标策略与闭市门禁，以及富途两笔美股盘中样本。HiThink 盘中时效和富途港股盘中样本按用户确认取消。后续来源资格由 [ThesisLedger 风险监控任务](../../../../thesis-ledger/docs/tasks/2026-11-06-risk-monitoring-schedule.md)跟踪。

- [x] 状态机：计划按当前来源返回可用性与600秒限制；代次/首来源改变拒绝。
- [x] 执行面：精确公开腾讯ETF，复用标准持久预算、断路器和凭据控制，不探测/调用受限来源。
- [x] 消费面：ThesisLedger根据规则生成需求、获取并写入既有观察日志；状态公开真实源时间与缺口。
- [x] 目标运行态与拒绝路径：2026-11-27 目标 DSA 的 7 类现行报价选路均可读取后台计划，`510300.SH` 执行入口按当前准入返回 `not_admitted`。
- [x] 富途账户快照：复用首选路由及现有会话校验，按同一 OpenD 端点限制每 30 秒 60 次快照；目标已保存港美股票／ETF 四项账户权限，四条后台计划通过，休市执行未触发报价。
- [x] 富途美股盘中实源与时效：2026-11-27 22:31（中国标准时间），通过目标 Server 后台协调器和真实 DSA，沿用 `futu/futu` 首选分别取得 `AAPL.US` 股票和 `SPY.US` ETF 报价。从来源时间到获取时间的延迟分别为 1.538 秒与 1.002 秒。两笔均为 `sample_observed`，策略 203 和主备顺序一致。[美股盘中证据](../../../../thesis-ledger/docs/evidence/2026-11-06-risk-monitoring-source-readiness.md#2026-11-27-富途美股盘中时效验收)
- [x] HiThink 后台动态额度：同一凭据的股票和 ETF 快照共用 DSA 本地预算，按回包余额和限流提示退避。2026-11-28 目标策略 203 下，`600519.SH` 和 `510300.SH` 后台计划与闭市门禁均验收通过。[动态额度与部署证据](../../../../thesis-ledger/docs/evidence/2026-11-06-risk-monitoring-source-readiness.md#2026-11-28-hithink-动态限流接入)
- [x] Yahoo 后台资格核对完成。按用户要求保持现行 yfinance 接入与后台状态。[两家来源复核](../../../../thesis-ledger/docs/evidence/2026-11-06-risk-monitoring-source-readiness.md#2026-11-27-hithink-与-yahoo-后台配额复核)

按用户确认，富途港股盘中时效验收移出本批。既有 OpenD 权限报告与业务选路继续适用。

2026-11-28 按用户确认取消 HiThink 的 A 股盘中时效验收。目标后台计划与闭市门禁的验收结果见上方证据。

2026-11-27 目标策略 203 的 7 类报价主备均保留。接入前拒绝证据、富途快更、正式权限检查和目标校验分别留存。[接入前核对](../../../../thesis-ledger/docs/evidence/2026-11-06-risk-monitoring-source-readiness.md#2026-11-27-目标后台报价验收) · [富途目标接入](../../../../thesis-ledger/docs/evidence/2026-11-06-risk-monitoring-source-readiness.md#2026-11-27-富途目标接入)


2026-11-06验证：由主仓维护的 `scripts/test-risk-source-dsa.mjs` 使用本地既有数据镜像，源码/测试工具只读挂载、network=none、临时SQLite、无真实来源调用；40项通过。覆盖新Data入口认证、严格请求、计划零Provider调用、固定generation/首目标、当前腾讯备选routeIndex=1保留、HiThink不调用、标准Runtime成功/失败/重启预算。随后数据闭包语法445文件、统一能力边界、runtime打包和测试登记门禁通过。pytest借用已有本地venv纯Python工具，生产镜像未安装测试依赖。

2026-11-06 主仓真实隔离 PostgreSQL/Redis 纵向链 9 项通过，DSA 传输与通知为受控 mock。当时没有部署、策略变更、凭据读取或更换、Provider 探测、订阅或真实通知。API 大文件由 1534 行降至 1530 行（与该轮开始前工作树比较），新增职责归后台来源模块。现有双语正文未修改，新中文专题记录新增合同。
