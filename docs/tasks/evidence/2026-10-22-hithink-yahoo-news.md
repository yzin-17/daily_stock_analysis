# HiThink 新闻接入与 Yahoo 实源验证证据

关联[规格](../../../../thesis-ledger/docs/specs/2026-10-22-hithink-yahoo-news.md)、[最终复核](../../../../thesis-ledger/docs/reviews/2026-10-22-hithink-yahoo-news-final-review.md)。本次完成 HiThink 的业务接入与 Yahoo 的真实读取验证，两者范围独立；原 AKShare/NVIDIA 的未变 DG3 证据继续复用。

## 来源和权限

- HiThink 已启用，现有控制配置版本 9、凭据版本 1；直接复用既有 Key。官方[个股分时新闻](https://fuyao.aicubes.cn/docs/api-reference/news-stock-timeline/)为 `GET /api/news/stock/timeline-list`，参数 `thscode`、`offset=first`、`size=1..100`，请求头 `X-api-key`。只使用这个公开接口，不调用明确未开放的[资讯事件库](https://fuyao.aicubes.cn/docs/api-reference/news-events/)，不抓正文。
- 新增一个精确绑定：`hithink / hithink-news-stock-timeline / CN / STOCK / NEWS_SEARCH`，manifest 版本 2；其他 HiThink 能力、配置与 HMAC 规则保持。
- Yahoo 的官方 yfinance SDK [Search](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Search.html)明确支持新闻；[Ticker.get_news](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_news.html)是另一条读取路径。本轮只验证当前宿主/目标安装，不安装或升级依赖，不创建 Yahoo 新闻准入和路线。
- Tushare 的原 120 分和独立权限记录保持，未调用其新闻接口；模型调用为零，研究写入关闭。

## 来源执行与受控验证

HiThink 固定 HTTPS 端点、单请求、总预算、512 KiB 与至多 100 条，禁止重定向；复用现有有界新闻进程和并发控制。只投影标题、媒体、内容 ID、跳转页及明确的毫秒时间戳。原始发表地址、来源首次可用时间及摘录保持未知；首屏和窗口覆盖不足返回 partial，原因 `timeline_window_not_exhaustive`，保留上游 `has_more` 对截断的影响。公共游标本轮不支持。

业务 `code=4001` 通过既有 rate_limited 错误表达，保留实际 HTTP 200；HTTP 429、权限失败、超时、结构/大小/URL/未知时间均有受控测试。凭据只经固定快照送往固定端点，不进入日志、参数或证据正文。

最初执行前误拒绝暴露了通用新闻桥对 `adapter.values` 的假设：HiThink 行情工厂可接受空快照，而新闻读取实际使用 `ReadContext.snapshot`。回归中曾尝试让原无路由工厂额外读取快照，旧行情的读取次数及晚到轮换场景随即失败；该改动已完整撤回，原 Runtime 文件摘要与本轮基线相同。最终由通用桥守住当前控制/快照，由真正读取 adapter 密钥的专用搜索适配层校验 adapter 的固定凭据；HiThink 读取当前上下文快照，未降低任何现行守卫。

在 DSA 根目录执行：

```bash
.venv/bin/python -B -m pytest -q tests/test_hithink_news.py tests/test_thesis_ledger_news_execution.py tests/test_thesis_ledger_news_gateway.py tests/test_thesis_ledger_hithink_credential_revision.py tests/test_thesis_ledger_hithink_credential_admission_runtime.py tests/test_thesis_ledger_hithink_quote_runtime.py tests/test_thesis_ledger_hithink_stock_runtime_v3.py tests/test_thesis_ledger_capability_registry.py tests/test_akshare_news.py tests/test_nvidia_news_rss.py
```

最终 497 通过、39.31 秒；四条警告为既有 Starlette 弃用及受控测试类收集提示。原 482 项中七个失败的中间工厂改动结果不作为通过依据，也未修改旧测试的读取次数或轮换断言。

其他最终验证：

- 主仓 `pnpm --filter @thesis-ledger/desktop exec vitest run test/market-news-sheet.ui.test.tsx test/market-news-api.test.ts`：6 通过；增加本次实际来源中文标签与首屏缺口显示，复用 CN/US 判断、显式读取、失败恢复和请求取消。
- 主仓 `pnpm --filter @thesis-ledger/desktop build`、定向 ESLint、import boundaries 通过；仅使用现有组件及原子类。Context Mode 的独立 PATH 找不到 pnpm 时，改用正常项目终端执行同一检查并通过，没有修改依赖。
- DSA 七个变更 Python 文件的 flake8 关键检查、能力边界和数据测试范围检查通过，新增测试在 `data-test-scope.json` 登记。
- infra `node --test scripts/nvidia-news-admission.test.mjs`：5 通过；在 infra 使用 DSA 虚拟环境并设置 `PYTHONPATH=<DSA绝对路径>` 执行 `python -B scripts/nvidia-news-admission.test.py`：8 通过。验证固定账号元数据配置档、默认计划只读、目标/锁/摘要/范围、事务内拒绝重复以及凭据变化拒绝。

## Yahoo 的真实结果

宿主与目标 Docker 均为 yfinance 1.7.0。每个环境各调用一次 `Ticker.get_news(NVDA, count=5)`，以及 `Search(NVDA/AAPL, news_count=5)`；使用 SDK 自带禁用缓存实现，仅进程内状态，不持久化 Cookie，不包含账户或持仓输入。宿主总执行上限 22 秒，目标 24 秒；Search 单请求超时 6 秒，未在 SDK 之外反复重试。

| 路径                    | 宿主                               | 目标 Docker              | 判定                                                                    |
| ----------------------- | ---------------------------------- | ------------------------ | ----------------------------------------------------------------------- |
| `Ticker.get_news(NVDA)` | 新闻 POST 两次 404，SDK 返回空列表 | 同样两次 404、空列表     | 当前 `finance.yahoo.com/xhr/ncp` 路径未通过，不能将空列表解释为没有新闻 |
| `Search.news(NVDA)`     | 新闻 GET 200、5 条、报价身份 NVDA  | GET 200、5 条、身份 NVDA | 搜索新闻路径可用                                                        |
| `Search.news(AAPL)`     | 新闻 GET 200、4 条、报价身份 AAPL  | GET 200、4 条、身份 AAPL | 第二个公开样本通过                                                      |

Search 的新闻端点为 `query2.finance.yahoo.com/v1/finance/search`。样本均有标题、HTTPS 链接、Unix 秒时间及包含本次代码的 `relatedTickers`；原始条数不等于独立证据组数，未来消费仍需沿用现有去重和有限覆盖语义。本次只证明两个美股样本的宿主/目标来源读取，未声称 Yahoo 业务适配、V3、Market 或 Web 已接入。

Cookie/crumb 的 SDK 引导请求与新闻请求分别记录；探针只保存去掉查询串的域名/路径、状态及公开新闻元数据，没有保存 Cookie、crumb 或密钥。结构化记录为私有 `yahoo-host.jsonl`、`yahoo-target.jsonl`。

## 正式更新、首次准入与政策

执行目录均为相邻 `thesis-ledger-infra`，正式入口退出 0：

1. `node scripts/research-state-backup.mjs backup --target thesis-ledger-dev --output <infra>/.maintenance-backups/hithink-yahoo-news-20261022`，保存 PostgreSQL、DSA、数据文件及回测。
2. `./scripts/sync-code.sh all` 完成代码同步与三个应用容器重启/健康检查。未变更 package manifest、requirements、Schema/migration、Dockerfile 或 SDK；DSA 镜像仍为 `sha256:ebe9d9a742b396b5e885f007dedc49c960010094d095545be9b33ff2dfa8b632`，Server/Worker 为 `sha256:6cf684b55348b9fe7e7869d5dedf5b50605c5b184602189d19f2034080f41140`。这只证明可写层代码已更新，未生成新镜像。
3. 目标正式读取函数直接取五条，435 ms，一次 HTTP 200。元数据审阅文件 SHA-256 为 `78e2215c410a9f29e1085bf4018037f44479b079943e33ed6f1c25cde219465d`，观察时间 `2026-10-22T06:39:22.349084+00:00`；只保留批准字段与原响应摘要。
4. 复用 `node scripts/nvidia-news-admission.mjs` 的计划、`--apply`、`--verify`；配置档 `--source=hithink-timeline`，确认 `--confirm=thesis-ledger-dev/hithink/600519.SH/account-news-metadata`，证据绝对路径与上述 `--sha256`/`--observed-at`，`--start=2026-10-19`、`--end=2026-10-29`、`--valid-hours=168`、`--recorded-by=hithink-news-20261022`。该用途标签不代表第三方内容转授权。
5. 仅新增版本 1 的准入及元数据证据，规范证据 SHA-256 `50a92e85a75a97c41cb8c4d5e418f19370dd220a65209bc2c69bd260d86846bf`。准入使用从只读配置得到的当前 HMAC 修订，并在写事务内重新核对；维护 Store 未初始化结构或轮换凭据。有效至 `2026-10-29T06:40:47.404212+00:00`，即北京时间 10 月 29 日 14:40:47，不自动续期。
6. 原 Market 政策 CAS 从 169 更新为 170，Server/DSA 均 applied、18 路线；只在原 CN 新闻路线前置 HiThink，AKShare 仍为备用，其余十七条逐字段相同。目录 status=complete、28 来源/192 精确目标。

## 目标 V3、Market 与 Web

- `/api/market-data/news`：HiThink、partial、五条、一次尝试、1325 ms；认证 DSA `/api/v3/thesis-ledger/market/news`：同一来源、同一五条元数据、一次尝试、1131 ms。来源修订 `hithink-stock-news-v1:hithink-stock-timeline-first-page-v1`，公开配置标识为 `control:config:9:credential:1`。
- 首条《部分中小酱香酒企减产》，媒体 21世纪经济报道，`sourceItemId=1_680969463`，跳转页 `https://news.10jqka.com.cn/20261022/c680969463.shtml`，发布时间 `2026-10-22T05:11:58+00:00`。五条标题、链接、媒体、时间、内容标识在 V3/Market 之间相同；没有原文地址、摘录或来源首次可用时间的伪证据。
- 实际 Web 使用宿主 Desktop 构建与目标 Docker API。显式读取显示“数据来源：HiThink 同花顺”、五条、首屏缺口和北京时间 13:11:58；控制台无错误。切换为 `000001.SZ` 立即清除旧结果，再读取显示未准入、零条和“未选定”来源，未混入旧新闻。
- Market 对未准入代码和 2020 旧窗口均 not_admitted、零尝试；不支持的游标为 unsupported、零尝试，两条候选来源的拒绝均保留。没有强行验证未开放账号权限。
- 原 AKShare/NVIDIA 来源的未变实际消费证据复用；本轮没有对目标制造真实故障或切换政策反复验备用，保留的备用关系由当前政策与通用执行测试支持。验证范围为 Web，不宣称 Electron 发布。

## 数据保全与运行态一致性

- 最终执行相同正式备份入口至 `<infra>/.maintenance-backups/hithink-yahoo-news-20261022-after`。PostgreSQL 83 表中 78 表摘要完全相同；其余为期望政策、历史增加一条、健康状态/检查增加一条，以及调度 `nextRunAt` 正常变化，COPY 与实际行规范化确认没有其他调度字段差异。Account=15、Position=1、LedgerEvent=85；AI 调用与研究观察仍零；57 个成功回测及归档字节相同。
- DSA 44 表结构相同，40 表内容相同；仅政策状态、历史新增一条、精确准入新增一条和健康诊断变化。健康新增 HiThink 新闻一条，旧 AKShare 新闻的 state/latency_ms/error_code/checked_at 在游标拒绝后刷新，其他原记录保持。所有配置和凭据密文不变，HiThink 配置仍 9、Tushare 仍 120 分。
- 205 个原 DSA 文件摘要相同，只增加本次一份元数据证据；未删除原文件、清库、恢复覆盖或删除 volume。28 来源中只有 HiThink manifest 的 version/capabilities/upstreamSources 按预期变化，其余 27 来源公开字段相同。
- 九个目标 DSA 生产文件与宿主 SHA-256 一致，其中原 Runtime 文件也与任务初始基线相同。源码保持未提交，其他工作区修改保留。
- 私有回执、前后公共状态、规范请求/响应、运行摘要与 Web 图片位于主仓 `.recovery-artifacts/remaining-p1-2026-10-20/hithink-yahoo-news-20261022/`。详细回执不代替上述正文断言与边界。
