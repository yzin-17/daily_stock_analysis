# 需求与验收规格

本目录维护需求目标、范围、行为、兼容约束和验收标准。长期系统事实、领域规则和 ADR 由[长期设计](../design/README.md)维护，执行状态由 Task 持有。

## 生命周期

Spec 按自身适用性独立判断归档：不再作为当前需求合同、且执行范围与验收已有明确收口或承接时，进入 `archive/specs/`；仍有效的需求合同继续保留。稳定设计的持续维护不依赖实施 Task 是否归档。

## 查阅入口

- [新闻准入工具的来源边界与显式选源](2026-11-14-news-admission-cleanup.md)

- [本地收费规则按需刷新与覆盖推进](2026-11-12-local-rule-demand-refresh.md)

- [竞技场公开网页搜索](2026-11-12-research-public-search.md)

- Yahoo 日线价格缺失调查已[归档](../archive/specs/2026-11-11-yahoo-daily-price-missing.md)，当前合同见[来源窗口设计](../design/thesis-ledger-yahoo-daily-window.md)。

- [腾讯日线完整周期窗口契约](2026-10-05-tencent-period-window.md)
- [股票历史状态按需读取](2026-10-09-history-data-on-demand.md)
- [DSA 纯数据服务收敛规格](2026-10-18-data-only-service.md)
- [基金披露日期证据的 DSA 适配](2026-10-21-fund-disclosure-date-evidence.md)
- [市场资料事实合同与执行规格](2026-11-03-market-reference-data.md)
- [衍生品与可转债资料适配规格](2026-11-04-derivative-reference-data.md)
- [分钟派生周期指标输入规格](2026-11-04-minute-provided-points.md)
- [公开来源市场资料适配规格](2026-11-04-public-reference-data.md)
- [首批备用来源语义核对规格](2026-11-04-reference-backup-qualification.md)
- [ETF有界公共收费来源规格](2026-11-05-etf-public-fee-sources.md)
- [MiniRacer原生运行时串行化规格](2026-11-05-miniracer-runtime-serialization.md)
- [官方收费来源版本监测规格](2026-11-05-rule-source-monitoring.md)
