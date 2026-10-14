# ThesisLedger 公告获取维护

## 自动入口

ThesisLedger 的已保存股票策略配置准备，会在冻结新快照前调用受 Control Token 认证的 `POST /api/v3/thesis-ledger/control/announcements/prepare`。请求为 `symbols`、`start` 和 `end`；响应返回 `preparing` 或 `ready`、任务标识、总数、完成数、失败数和检索／下载／解析统计。接口只入队和读取进度，网络获取在后台执行。

只有生效路由启用、`official-states` 数据源启用，且中国内地股票历史状态路由选用了 `cn-stock-daily-suspension` 或 `cn-stock-risk-price-limits` 时自动获取。停用或移除这些来源后不新增后台获取。普通公告列表、状态读取、快照计算和旧回测重放不调用上游。

默认每天上海时间 22:00 更新三十天内用于回测准备的证券，回查最近七天涉及的月份。后台更新不延长证券的使用期限。进程重启后自动处理积压；执行中的旧任务租约最多五分钟，过期可重新领取，最多三次尝试。获取器在查询和下载之间检查三分钟时间预算；查询超时为二十秒，原文下载超时为二十五秒。

## 存储与复用

库存位于 `DATABASE_PATH` 所在目录的 `thesis-ledger-announcements/`：顶层 JSON 是公告元信息；`originals/` 按 SHA-256 保存原文；旧的公告标识 PDF 仍可读取。`.queries/` 保存成功且未截断的精确查询回执，`.acquisition/` 保存持久任务、最近使用记录及每日调度标记。整个目录应随现有 DSA 数据卷保留。

历史查询及核实过摘要的原件复用；涉及最近七天的月份六小时内复用。检索完成、原文下载和事实解析分别记录；没有结果不能解释为正常交易。下载或解析失败保留已有可用资料。资料中不明确的日期、风险类别或比例保持未知，不推断整段历史状态。

## 显式维护

原有 `POST /api/v3/thesis-ledger/control/announcements/fetch` 和 CLI 获取入口保留；公告目录由 `GET /api/v3/thesis-ledger/market/announcements` 查询。准备进度与目录内容不是同一个数量，解析不支持的原文仍可在目录查看。

自动获取的产品范围与目标验收，以相邻 ThesisLedger 仓库的 `docs/specs/2026-10-08-announcement-preparation.md` 及对应实施记录为准。资料进入库存不代表已取得严格历史首次公开资格。
