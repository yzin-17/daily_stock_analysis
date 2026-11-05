# Yahoo 新闻 DG3 实源扩展证据

## 范围与原证据

本次用户授权将 `Search.news` 接入现有新闻功能，并继续 DG3、保留现有数据。[规格](../../../thesis-ledger/docs/specs/2026-10-22-yahoo-news-dg3.md)与[任务](../../../thesis-ledger/docs/archive/tasks/2026-10-22-yahoo-news-dg3.md)拥有当前要求和状态。本文件记录具体命令、输入、结果及边界。

复用[前次证据](2026-10-22-hithink-yahoo-news.md)：yfinance 1.7.0 宿主及 Docker Search 对 NVDA/AAPL 成功，旧 Ticker 路径 404；HiThink/AKShare/NVIDIA 的真实消费、未改共享合同及 Server 证据保持。原 DG3 的有限来源验收不被重新打开；本轮补充 Yahoo 的精确绑定与实际业务消费。研究 G3 的模型、预算和写入边界不变。

## 本地验证

- DSA：`.venv/bin/python -B -m pytest -q tests/test_yahoo_news.py`，33 项通过，两个既有测试框架弃用警告。覆盖秒时间/未知时间、身份和关联代码、转载归组、SDK 调用及缓存恢复、网络白名单/次数/大小/总预算、真实 404/429 分类、精确准入/日期/游标/关闭来源零尝试。测试不访问真实网络。
- infra：`node --test scripts/nvidia-news-admission.test.mjs`，6 项通过；`PYTHONPATH=<DSA> <DSA>/.venv/bin/python -B scripts/nvidia-news-admission.test.py`，9 项通过。固定 `yahoo-search` profile 仅允许 `yfinance/NVDA.US/public-metadata-research`，元数据证据不含正文；原三个 profiles、首次写保护与事务检查通过。

## 受影响回归与修正

完整定向命令在 DSA 根目录执行：

```sh
.venv/bin/python -B -m pytest -q tests/test_yahoo_news.py tests/test_thesis_ledger_news_gateway.py tests/test_thesis_ledger_news_fallback.py tests/test_thesis_ledger_news_execution.py tests/test_thesis_ledger_capability_registry.py tests/test_thesis_ledger_yfinance_capability_path.py tests/test_yfinance_financial_period.py tests/test_yfinance_fundamental_adapter.py tests/test_yfinance_hk_bare_code.py tests/test_yfinance_hk_indices.py tests/test_yfinance_jp_kr_indices.py tests/test_yfinance_normalize.py tests/test_yfinance_us_indices.py
```

最终 483 项通过，46.25 秒；四个既有弃用/受控测试类收集警告。首次结果为 482 通过、1 项失败：旧目录测试只允许两个 Yahoo 参考子来源。原九项日线绑定与修订未变，精确增加新闻子来源断言后复验通过。另将 Curl 传输延迟加载，保持缺少可选 SDK 时注册目录的既有行为，不让新闻扩展变成启动时硬依赖。

Desktop：`pnpm --filter @thesis-ledger/desktop exec vitest run test/market-news-sheet.ui.test.tsx test/market-news-api.test.ts`，7 项通过；`pnpm --filter @thesis-ledger/desktop build` 通过，仅有既有大 chunk 提示。只新增来源标签，DOM、样式和请求合同不变。

门禁：六个受影响 Python 文件的关键 flake8 检查零错误；`scripts/check_thesis_ledger_capability_boundaries.py` 通过；`scripts/data_test_scope.py` 为 384 个数据文件/386 个目标；主仓定向 ESLint、`node scripts/check-boundaries.mjs` 通过。已安装 shadcn CLI 文档与组件配置确认原 Badge/Alert 可直接复用，没有安装或覆盖组件。

## 更新前保全

执行目录为 infra，正式入口 `node scripts/research-state-backup.mjs backup --target thesis-ledger-dev --output <infra>/.maintenance-backups/yahoo-news-dg3-20261022`，退出 0。包含 PostgreSQL、DSA SQLite、DSA 文件、57 个成功回测及镜像身份。回测包摘要为 `e0928461e9642b1b09d97cb14c69622760f4fc6fb850c6faabc7fb2b991b7348`。公开控制面基线为政策/DSA 170、18 路线；Yahoo 已启用、配置版本 0，Tushare 仍为 120 分/版本 3。

## 正式更新与目标身份

在 infra 根目录执行 `./scripts/sync-code.sh all`，使用保留并发进程锁检查的执行权限，退出 0。Server/workspace 在宿主构建，DSA、Server、Backtest Worker 正式同步并重启，全部健康；PostgreSQL、Redis 亦健康。未更改依赖、结构或卷，镜像不变：

- DSA：`sha256:ebe9d9a742b396b5e885f007dedc49c960010094d095545be9b33ff2dfa8b632`。
- Server/Worker：`sha256:6cf684b55348b9fe7e7869d5dedf5b50605c5b184602189d19f2034080f41140`。

九个关键生产文件的宿主/目标摘要一致，包含三个新 Yahoo 模块、既有 Yahoo 来源、新闻 Query/Execution/Results/Gateway 与 API。该结果证明容器可写层运行代码，不能视为镜像更新；重建后仍须正式同步或完整更新。

## 真实读取、首次准入与政策

目标 `fetch_yahoo_news('NVDA.US', ..., max_results=6)` 首次真实读取成功：3,912 毫秒、六条元数据，引用股票为 NVDA/EQUITY，每条 `relatedTickers` 均包含 NVDA。四次 HTTP 为两次 Cookie 引导 404、一次 getcrumb 200、一次搜索 200；引导失败没有冒充新闻失败，所有实际请求计数，查询串、Cookie 和 crumb 未进入证据。原始响应摘要保留，审阅投影文件只含已允许的公开元数据。

正式维护入口在 infra 执行 `node scripts/nvidia-news-admission.mjs`，参数为 `--source=yahoo-search`、`--confirm=thesis-ledger-dev/yfinance/NVDA.US/public-metadata-research`、已审阅元数据文件、`--sha256=89716c0a88ba856e91b7daf8246ed05f0daced1c8b2c617cc7882065dd742bb6`、`--observed-at=2026-10-22T07:40:24.279711+00:00`、`--start=2026-10-19`、`--end=2026-10-29`、`--valid-hours=168`、`--recorded-by=yahoo-news-20261022`。默认计划、`--apply` 及 `--verify` 均退出 0。

首次准入版本 1，凭据修订 `not-required`，Adapter `yfinance-search-news-1-7-0-v1`、来源 `yahoo-search-recent-news-v1`。规范证据摘要为 `50866b1159b5969c6bef2fcb50114364ee92b4669249998c41557c81ba224e9d`，有效至 `2026-10-29T07:42:49.418235+00:00`（北京时间 15:42:49），不自动续期或扩大标的。

通过现有 `PUT /api/market-data/policy`，以 expectedRevision 170 比较交换为 171；DSA 171、`syncState=applied`。18 条原路线均保留，仅 US/STOCK/NEWS_SEARCH 变为 `yfinance/yahoo-search-news` 首源、原 `nvidia-news/nvidia-newsroom-rss` 备用，其余十七条逐对象相同。目录为 complete、28 来源/193 精确目标；公开来源记录仅 Yahoo 的 version/capabilities/upstreamSources 变化，其余二十七个来源完全相同。

## DG3 真实消费与页面

以规范 NVDA.US、最近三天、limit 5、预算 15,000 毫秒分别调用 `POST /api/market-data/news` 与带现有内存令牌的 `POST /api/v3/thesis-ledger/market/news`。V3 用时 5,349 毫秒；两次均读取六条、投影五条/五组，标题、URL、发布者、来源条目标识、秒时间、转载链接和证据组逐项一致。实际来源均为 Yahoo，政策修订 `effective:171`、上述 Adapter/来源修订及 `not-required` 相同，四次上游尝试，errors 为空。

两端状态均为 partial，`complete=false`，保留 `pagination_truncated` 与 `search_first_page_not_exhaustive`。所有摘要、originalUrl、sourceAvailableAt 为 null；没有用抓取时间补发布时间或首次可用时间。受控测试另证明相同内容的不同 URL 会归为一组，不把转载重复计算为独立证据。

验收辅助脚本最初把 Market.source 错当作只有两个字段，因响应同时保留路由修订而停止。修正的是辅助断言，生产代码未变；原已成功的 Market 响应被复用，没有重复该上游请求，故其延迟记为未知。后续真实 V3 与已保存 Market 的五条记录完全一致。

目标负向读取：AAPL.US 未准入、2020 年窗口均为 not_admitted、零尝试/零条；不支持的游标在主备两来源均返回 unsupported、零尝试/零条。没有请求未开通的 Tushare 接口或扩大准入。

实际 Web 使用本轮 Desktop 构建连接上述目标 API。NVDA.US 展示 Yahoo Finance 新闻、五条/五组、真实媒体/链接/秒时间、未知首次可用时间及近期第一页缺口；页面抓取于北京时间 15:50:35。近期搜索会变化，页面的较晚快照不冒充先前 API 的同一批源文。切换 AAPL.US 后旧结果立即清除，实际读取显示暂不可用、零条、来源未选定及未准入。控制台 error 为空；临时页面和预览进程已关闭。未执行 Electron 或原生发布。

## 更新后保全与差集

同一正式备份入口输出至 `<infra>/.maintenance-backups/yahoo-news-dg3-20261022-after`，退出 0。仅只读解析两份备份；没有恢复、清库、重置或删卷。

- PostgreSQL：83 表结构和数量一致，76 表完整行摘要相同。15 个 Account、1 个 Position、85 个 LedgerEvent 的内容不变；AiInvocation/AiInvocationFact/ResearchObservation 均仍为零。
- 七个差异表中，DesiredProviderPolicy 与修订表仅本次 170→171；ProviderHealth 仅检查时间/延迟等诊断字段，ProviderHealthCheck 增加两条。其余三个是原已启用“盘后估值预估”定时任务于北京时间 16:00:15 自然成功执行：AutomationRun/Lease 各增一条，AutomationJob 仅 lastRunAt/nextRunAt 变化，没有手动触发或新增启用。
- DSA：44 表结构相同，40 表内容相同；四项差异为政策历史/当前政策、来源诊断和首次准入 5→6。全部原配置和加密凭据表不变。Yahoo 配置仍启用/版本 0；Tushare 仍启用、120 分/版本 3，HiThink/AKShare/NVIDIA 原配置和准入保留。
- 追加核验：原五条准入记录和原政策历史的全部行均逐行包含在更新后备份中，未覆盖或删除旧记录。
- 全部 206 个原 DSA 文件逐内容摘要保持，没有删除或改写；唯一新增为上述规范摘要的准入元数据文件。没有新增 Cookie 或时区持久缓存。
- 57 个成功回测保持；前后 `backtest.tar` 字节相同，摘要仍为 `e0928461e9642b1b09d97cb14c69622760f4fc6fb850c6faabc7fb2b991b7348`。

## 证据位置与最终边界

完整公开响应、元数据投影、准入三阶段回执、政策前后、九文件摘要、保全差集及页面图文保存在主仓忽略目录 `.recovery-artifacts/remaining-p1-2026-10-20/yahoo-news-dg3-20261022/`；两份数据备份位于上述 infra 忽略目录。首次源码基线的已有文件均保留，受影响文件只在本主题写集内，未提交或改写无关 WIP。

YD-AC1～5 的实现、目标联通和保全已通过，详细最终复核见[主仓报告](../../../thesis-ledger/docs/archive/reviews/2026-10-22-yahoo-news-dg3-final-review.md)。HiThink/AKShare/NVIDIA、未改 Server 和共享合同证据按原输入复用；没有为了补验收人为制造真实主源故障。当前准入仅 NVDA.US 近期窗口，AAPL 旧 SDK 验证不等于业务准入；研究 G3、获准模型、预算及发布条件保持独立。

最终文档核对：本主题十五个本地链接均有效，归档 Task 六个实施叶已勾选且无未完成项；三仓 `git diff --check` 为零，受影响代码与新复核报告格式检查通过。源码基线的已有文件均存在，差异仅属本主题代码、测试、准入工具及状态文档；保留无关 WIP。
