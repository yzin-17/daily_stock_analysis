# 财联社与华尔街见闻 DG3 实源验收证据

## 范围与输入

用户授权接入两家新闻，复用 a-stock-data 和既有探测，完成 DSA、Docker、Server/Web 验收并保留数据。主仓[规格](../../../../thesis-ledger/docs/specs/2026-10-22-market-flash-news-dg3.md)拥有合同，[任务](../../../../thesis-ledger/docs/archive/tasks/2026-10-22-market-flash-news-dg3.md)拥有状态；本文件只保留实际命令与分层证据。

复用上游 `29b07d2e37005b9fd568bca1262f6e572b5f9407` 的财联社签名、固定端点和见闻 A 股频道方法，保留 `Copyright 2026 Simon Lin` 及 Apache-2.0 正文。前序主机探测的两个 HTTP 200/有效页直接复用；本轮只补生产读取器、目标、准入及实际消费。HiThink、AKShare、Yahoo 和 NVIDIA 的未变实源证据不重跑；Tushare 120 分及研究模型边界保持。

两家流均缺少可靠逐条证券关联。新增 `NEWS_FEED/news-feed-v1`，与 `NEWS_SEARCH/news-search-v1` 分开：`market: CN` 是消费路由范围；财联社为全市场电报，见闻为 A 股频道，条目 `relatedInstruments=[]`。不把其中的海外/宏观消息标为某只 A 股的相关新闻。

## 本地与受控验证

| 检查 | 命令与结果 |
| --- | --- |
| DSA 受影响回归 | 从 `tests/data-test-scope.json` 选取 19 个新闻及精确注册/Yahoo 能力文件，`python -m pytest ... -q`：711 通过，63.40 秒；其中新读取/合同/认证入口 37 项 |
| 频道显示修正复核 | `python -m pytest tests/test_thesis_ledger_news_feed.py -q`：9 通过，4.02 秒；只修改来源频道说明，未重跑已通过外部请求 |
| 原合同 | 原新闻 DTO/结果组 49 项通过，已包含在上述回归；个股身份、窗口和证据约束保持 |
| 共享 Schema | `pnpm --filter @thesis-ledger/schemas exec vitest run test/market-news-feed.test.ts test/market-news-v3.test.ts test/market-news-read.test.ts`：69 通过；清理未使用变量后的新合同 9 项再次通过 |
| Server | 三个新闻测试文件共 67 项完成验证；其中新快讯 5 项、原协议 31 项、真实受控 HTTP 31 项。协议错误先转标准 `DsaError`，坏哈希明确不可用；相应 5 项修复复核通过 |
| Web | 新闻 API 与 UI 两文件 9 项通过，包括显式请求、切换来源取消旧请求、关闭释放请求和中文来源/缺口文案 |
| 维护入口 | `node --test scripts/nvidia-news-admission.test.mjs`：7 通过；设置 DSA `PYTHONPATH` 后执行 `scripts/nvidia-news-admission.test.py`：10 通过。固定来源/范围、无正文元数据、旧准入及并发拒绝保持 |
| 类型与构建 | Schema/Server/桌面类型检查与 Server/桌面构建通过；桌面最后构建连接目标 API，用于 Web 验收 |
| 数据门禁 | `scripts/data_test_scope.py`：386 数据文件/388 目标；`check_data_service_boundary.py --syntax`：386 运行文件、通过；关键 flake8 通过 |
| 主仓门禁 | `check-boundaries.mjs` 通过，补充快讯只经 Market 消费的自动约束；本轮源码 ESLint 错误修正后通过。全仓 lint 脚本会扫描既有 `.recovery-artifacts` 恢复快照并报 504 个项目归属解析错，本轮不修改忽略规则，不把该次全仓命令写成通过 |

命令日志和本轮文件摘要位于主仓私有目录 `.recovery-artifacts/remaining-p1-2026-10-20/market-flash-news-20261022/`。只保存公开来源元数据、测试与摘要，令牌只在内存使用。

## 正式 Docker 与控制面

1. `research-state-backup.mjs backup --target thesis-ledger-dev` 保存前置 PostgreSQL、DSA SQLite/文件及回测备份。
2. 正式 `sync-code.sh all` 退出 0；随后为准确显示“财联社全市场电报/见闻 A 股频道”，使用最小 `sync-code.sh dsa` 再同步显示修正。DSA、Server、Worker 均健康，14 个本轮 DSA 生产源文件和 5 个 Server 产物与宿主摘要相同。
3. 镜像保持原值：DSA `ebe9d9a742b3…`，Server/Worker `6cf684b55348…`。这是容器可写层同步，重建后需再次正式同步或完整更新，不是新镜像发布。
4. 经 Server 正式配置分别启用 `cls`、`wallstreetcn`，配置版本均 1、无需 Key。既有来源配置未重存。
5. 生产读取器在目标容器分别读取一页：财联社 5 条/261ms，见闻 5 条/620ms，各 HTTP 200、1 次尝试。只保存 ID、标题投影、URL 和时间等准入元数据。
6. 维护入口 `nvidia-news-admission.mjs` 的两份固定档案分别完成计划、首次应用、只读核验。`CN.STOCK` 是市场作用域标识；日期 2026-10-19～29、记录版本 1、`not-required`。有效至北京时间 10-29 21:02:53（财联社）及 21:03:08（见闻）；不覆盖、扩展或自动续期。
7. 通过 CAS 只新增 `CN/STOCK/NEWS_FEED`，目标为 `cls/cls-roll`、`wallstreetcn/wallstreetcn-a-stock`。政策/DSA 171→172、18→19 路线，原 18 条逐项保持。最终目录 complete，30 来源/195 精确目标，两项快讯均 ready。

## DSA 与 Server 实源

| 来源 | Market / V3 耗时 | 返回与独立分组 | 实际事实 |
| --- | --- | --- | --- |
| 财联社 | 698ms / 586ms | 5 / 5 | 两层相同 5 条，各 1 次上游尝试，`cls-roll`、`effective:172`；检查 5 条 |
| 华尔街见闻 | 2658ms / 670ms | 5 / 5 | 两层相同 5 条，各 1 次上游尝试，`wallstreetcn-a-stock`、`effective:172`；检查 6 条后限为 5 条 |

两家均 `partial` / `current-feed` / `complete=false`；披露单页有限覆盖、条数截断及必要的“标题取自来源已有快讯文本”。没有补写摘要、原文归属或首次可用时间，证券关联为空。未知来源首次可用时间保持 `null`。

Market 三个真实反例均 `unavailable`、空列表、上游尝试 0：未配置 Provider 与 2020 历史窗口为 `not_admitted`，不支持游标为 `unsupported`。未改路由制造故障，也不把两家独立正向读取等同于真实故障切换验收。

## Web 实际消费

当前桌面构建通过 Vite 预览连接目标 Docker Server/DSA。进入“新闻 → 市场快讯”，财联社与见闻分别显式读取，各显示 5 条/5 组、中文实际来源、发布时间与公开链接、未知首次可用时间和有限覆盖；切换来源先清除旧结果。浏览器控制台 error 为 0。

截图与可访问文本：`cls-web-positive.png/.txt`、`wallstreetcn-web-positive.png/.txt`；`web-console-errors.json=[]`。财联社抓取时间 21:04:36，见闻 21:05:35，均为本次 Web 新取数。快讯内容会更新，Web 列表不要求与早一分钟的 API 快照永远相同。个股入口保留，旧源实源按未变输入复用。本项不代表 Electron/原生打包发布。

## 逐对象数据保全

前后备份位于 infra `.maintenance-backups/market-flash-news-20261022` 和 `...-after`；只读解码 PostgreSQL 备份、以只读 SQLite 和 tar 内容摘要比较。`retention.json` 与 `retention-assertions.json` 通过：

- PostgreSQL 83 表结构保持，78 表完整内容相同。15 账户、1 持仓、85 账本事件及其内容保持；AI 调用/事实、研究观察仍为 0。
- PostgreSQL 变化限一条新政策历史及当前政策、两条来源健康检查和相应诊断，以及原启用“盘中估值采样”的自然 `nextRunAt` 更新；未新建或人工触发自动任务。
- DSA 44 表结构保持，39 表相同。原 4 行来源配置、原 6 条准入及原健康/政策历史记录逐行保持；只增加两家配置、两份准入、两项健康记录及政策更新。旧凭据密文未变化。
- 原 207 个 DSA 文件全部内容相同，只新增两份与正式准入摘要匹配的公开元数据证据。
- 57 个回测的归档字节完全相同，SHA256 为 `e0928461e9642b1b09d97cb14c69622760f4fc6fb850c6faabc7fb2b991b7348`。三个应用镜像身份保持，原 18 路线保持。

本轮实源扩展已满足规格范围；研究 G3 的模型、原预算、报告/引用/历史读取门禁仍由原任务持有。其他模块盘点归主仓[后续范围审查](../../../../thesis-ledger/docs/reviews/2026-10-22-astock-data-module-followups.md)及 TODO，不改变当前实源完成条件。
