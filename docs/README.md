# DSA 文档中心

本仓维护 ThesisLedger 的独立数据服务。文档分类见[文档指南](DOCUMENTATION-GUIDE.md)，运行入口见[项目说明](../README.md)。

## 当前查阅入口

| 内容 | 入口 |
| --- | --- |
| 数据服务的范围与保全合同 | [纯数据服务规格](specs/2026-10-18-data-only-service.md) |
| 数据接口、来源能力及适配边界 | [长期设计](design/README.md) |
| 需求范围与验收 | [产品规格](specs/README.md) |
| 凭据、NAV 生产及账户权限 | [运维指南](guides/README.md) |
| 验证记录与固定样本 | [验证证据](evidence/README.md) |
| 股票目录维护 | [Tushare 股票列表指南](guides/TUSHARE_STOCK_LIST_GUIDE.md) |
| 贡献与许可证归属 | [贡献指南](guides/CONTRIBUTING.md)、[英文版](guides/CONTRIBUTING_EN.md)、[公开来源借鉴说明](guides/2026-11-04-astock-public-source-attribution.md) |
| 版本变化 | [CHANGELOG](CHANGELOG.md) |
| 已完成任务与原产品资料 | [归档](archive/README.md) |

## 文档边界

`design/` 保存长期接口与适配设计，`specs/` 保存需求与验收合同，`guides/` 保存使用、开发和维护说明，`evidence/` 保存验证材料，`archive/` 保存已完成或被替代资料。新任务和独立审查按实际需要建立目录。

原分析产品、LLM、Bot、告警、WebUI 和桌面客户端的资料位于 [archive/legacy/](archive/legacy/README.md)，保留原语言、历史命令和关联素材。当前服务的研究、模型、账户与客户端消费由[ThesisLedger](../../thesis-ledger/docs/README.md)维护，部署编排由[infra](../../thesis-ledger-infra/docs/README.md)维护。

## 当前任务

最近核对：2026-11-05。本仓已无活动 Task；历史数据按需读取的本仓实现与主仓最终验收已对账，记录已[归档](archive/tasks/2026-10-09-history-data-on-demand.md)。纯数据服务和腾讯周期窗口任务亦已完成，见[归档入口](archive/README.md)。

当前合同继续从 [specs/](specs/) 和[架构说明](design/README.md)查阅。`evidence/` 中被跨任务、主仓或 CI 引用的记录保留稳定位置，不作为另一套当前任务状态。

Tushare 真实现金消费、研究价格复核与基金盘中估值的剩余条件仍由[主仓任务](../../thesis-ledger/docs/tasks/README.md)维护；本仓无活动任务不表示这些产品门禁通过。
