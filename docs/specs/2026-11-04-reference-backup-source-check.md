# 首批备用源固定采证契约

对应[任务](../../../thesis-ledger-infra/docs/archive/tasks/2026-11-04-reference-backup-source-check.md)，承接[主仓规格](../../../thesis-ledger/docs/specs/2026-11-04-astock-source-coverage.md)。扩展现有 `../daily-stock-analysis/scripts/local-runtime/public-reference-source-check.py`，分别固定同花顺三表、沪深两融、沪深龙虎榜与新浪研报的端点／参数；日期与证券严格校验，不接受任意 URL、请求头或凭据。

默认显示计划，显式执行才读取；每次独占保存 permit、完整原件和回执。沿用正常 TLS、无隐式 netrc、无自动跳转／重试与字节边界；不得复制参考代码关闭 TLS 的片段。错误、限频、未发布、HTML 与实际数据分别保留，不以 200 自动判合格。

首批八次代表读取，0 USD，无模型／通知、配置、路由、准入、Docker 或业务变更。后续参数纠正或来源说明读取须有原件／源码依据并逐次记录。CLI 证明公开路径读取，不代表目标产品可用；原来所有采证类型保持兼容，采用现有依赖与测试入口。

已按实际父响应加入四条固定后续：上交所两融已证实的第二页、深交所同目录JSON、深市父页唯一证券／日期／原因的详情链接，以及新浪明确迁移地址的HTTPS读取。必须校验父profile、日期、请求身份、原件摘要、状态和来源主机；迁移不接受任意目标，详情不执行JavaScript或外部链接。首批及后续共12次数据接口尝试，原302与原件保留，产品选择未改变。
