# 历史文档归档

已完成实施记录保留原任务 ID、证据和失败边界；现行合同继续留在 `specs/`。当前执行从[任务索引](../README.md)进入。

## 已完成任务

- [2026-11-14 新闻准入工具残留清理](tasks/2026-11-14-news-admission-cleanup.md)：通用入口、显式选源与消费者已收敛，验证结果见任务链接。

- [精确K线缓存控制上下文](tasks/2026-11-12-bar-cache-context.md)、[前置门禁合同收敛](tasks/2026-11-12-cache-prerequisite-gate-reconciliation.md)及[腾讯日线响应窗口修复](tasks/2026-11-11-tencent-daily-response-window.md)：完整门禁、正式生产者与目标消费分别验收；共享证据继续保留稳定位置。

- 复权日线非对称能力核实：[规格](specs/2026-11-11-adjusted-daily-capability-audit.md)、[任务](tasks/2026-11-11-adjusted-daily-capability-audit.md)、[上游与窗口证据](../evidence/2026-11-11-adjusted-daily-capability-audit.md)。

- 沪深 ETF 来源与富途接入：[规格](specs/2026-11-11-futu-cn-etf-daily.md)、[任务](tasks/2026-11-11-futu-cn-etf-daily.md)、[真实窗口证据](../evidence/2026-11-11-futu-cn-etf-daily.md)。

- Yahoo 日线价格缺失：[规格](specs/2026-11-11-yahoo-daily-price-missing.md)、[任务](tasks/2026-11-11-yahoo-daily-price-missing.md)、[真实窗口证据](../evidence/2026-11-11-yahoo-daily-price-missing.md)。

- [腾讯周期窗口实施与验收](tasks/2026-10-05-tencent-period-window.md)
- [股票历史状态按需读取实施记录](tasks/2026-10-09-history-data-on-demand.md)
- [DSA 纯数据服务实施任务](tasks/2026-10-18-data-only-service.md)
- [基金披露日期证据 DSA 实施任务](tasks/2026-10-21-fund-disclosure-date-evidence.md)
- [市场资料事实合同与执行任务](tasks/2026-11-03-market-reference-data.md)
- [衍生品与可转债适配任务](tasks/2026-11-04-derivative-reference-data.md)
- [分钟派生周期指标任务](tasks/2026-11-04-minute-provided-points.md)
- [公开来源市场资料适配任务](tasks/2026-11-04-public-reference-data.md)
- [首批备用来源语义核对任务](tasks/2026-11-04-reference-backup-qualification.md)
- [ETF公共收费来源任务](tasks/2026-11-05-etf-public-fee-sources.md)
- [MiniRacer原生运行时串行化任务](tasks/2026-11-05-miniracer-runtime-serialization.md)
- [官方收费来源监测任务](tasks/2026-11-05-rule-source-monitoring.md)

## 原产品资料

[原产品历史区](legacy/README.md)保存源码退役后的指南、LLM、Bot、客户端、旧 API 规格和素材。共享验证资料继续留在 [evidence/](../evidence/)，不重复复制。

本轮核对见[三仓整理记录](../../../thesis-ledger/docs/archive/reviews/2026-11-05-three-repository-documentation.md)。
