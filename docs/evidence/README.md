# DSA 验证证据

本目录统一保存验证记录、截图、XML、JSON 和被测试或维护脚本读取的固定样本。文件按日期与主题定位，保留原文件名、任务 ID、输入版本和验证层级；原始来源响应、签名与摘要样本保持字节内容。

- 当前执行、未满足条件和最新证据指针由[任务索引](../README.md)及对应 Task 维护。
- 独立审查正文按三仓规则归入 `reviews/`；当前来源验证的输入、判断与边界从下方主题入口查阅。
- 多任务、历史主题或 CI 共用的证据保持单一正文和稳定路径；任务完成时可继续引用。
- 已结案且仅属于归档任务的证据可进入 `docs/archive/evidence`，归档时同步修正所有引用。

文档分类与生命周期见[文档指南](../DOCUMENTATION-GUIDE.md)。保存证据不扩大来源、模型、通知、部署或受保护数据操作的授权。

## 来源验证入口

- [目录来源](thesis-ledger-catalog-source-evidence.md)
- [基金持仓来源](thesis-ledger-holdings-source-evidence.md)
- [基金净值来源](thesis-ledger-nav-source-evidence.md)
- [报价来源](thesis-ledger-quote-source-evidence.md)
- [RQData 身份读取](thesis-ledger-rqdata-identity-evidence.md)：本地、目标错误层与真实账号正向消费分别解释，后者不由记录存在推定通过。
