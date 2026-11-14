# DSA 验证证据

- [本地收费资料按需刷新](2026-11-12-local-rule-demand-refresh.md)：受控覆盖推进、真实文档意外读取与网络隔离修复，现行费用认证和部署边界分别保留。

- [缓存前置门禁合同收敛](2026-11-12-cache-prerequisite-gate-reconciliation.md)：原32项失败的合同归属、分组验证及最终完整门禁独立证据。

- [腾讯当前日线绑定真实验收](2026-11-12-tencent-daily-live-verification.md)：旧次数限制解除后的真实读取、严格日期裁剪及完整门禁分层结果。

本目录统一保存验证记录、截图、XML、JSON 和被测试或维护脚本读取的固定样本。文件按日期与主题定位，保留原文件名、任务 ID、输入版本和验证层级；原始来源响应、签名与摘要样本保持字节内容。

- 当前执行、未满足条件和最新证据指针由[任务索引](../README.md)及对应 Task 维护。
- 独立审查正文按三仓规则归入 `reviews/`；当前来源验证的输入、判断与边界从下方主题入口查阅。
- 多任务、历史主题或 CI 共用的证据保持单一正文和稳定路径；任务完成时可继续引用。
- 已结案且仅属于归档任务的证据可进入 `docs/archive/evidence`，归档时同步修正所有引用。

文档分类与生命周期见[文档指南](../DOCUMENTATION-GUIDE.md)。保存证据不扩大来源、模型、通知、部署或受保护数据操作的授权。

## 来源验证入口

- [复权日线非对称能力核实](2026-11-11-adjusted-daily-capability-audit.md)：腾讯股票后复权、BaoStock ETF 日线，以及 HiThink／Yahoo 口径和 Tushare 账号限制。

- [沪深 ETF 来源与富途接入](2026-11-11-futu-cn-etf-daily.md)：来源口径矩阵、沪深 ETF 完整窗口及账号实读边界。

- [Yahoo 日线价格缺失](2026-11-11-yahoo-daily-price-missing.md)：原始响应分层、单日补取、股票／ETF 完整窗口及控制状态保全。

- [回测日线来源适用性](../../../thesis-ledger/docs/evidence/2026-11-11-backtest-source-compatibility.md)：上市未知适配、完整窗口、真实来源失败原因及调用预算。

- [富途与 HiThink 筹码账号核查](2026-11-09-chip-provider-accounts.md)：区分外部开放状态、现有认证和真实只读结果。

- [目录来源](thesis-ledger-catalog-source-evidence.md)
- [基金持仓来源](thesis-ledger-holdings-source-evidence.md)
- [基金净值来源](thesis-ledger-nav-source-evidence.md)
- [报价来源](thesis-ledger-quote-source-evidence.md)
- [RQData 身份读取](thesis-ledger-rqdata-identity-evidence.md)：本地、目标错误层与真实账号正向消费分别解释，后者不由记录存在推定通过。
