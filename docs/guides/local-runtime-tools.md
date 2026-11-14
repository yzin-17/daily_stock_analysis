# 本地运行工具

以下维护命令从同级 `thesis-ledger-infra` 目录执行；执行仍须遵守[工作区规则](../../../AGENTS.md)。具体目标、业务保护、外部调用和授权条件以各入口为准。

## 招商银行的同花顺个股新闻

### 个股新闻分页修订升级

部署 `hithink-stock-news-offset-v2 / hithink-stock-timeline-offset-v2` 后，既有首屏修订准入会失效。使用以下专用入口核验真实前后两页并升级同一记录；它保留原有标的、日期范围、生效与到期时间、凭据和政策，仅更新适配修订及证据链。计划版本或摘要变化时重新查看计划，不直接改库。

```sh
node ../daily-stock-analysis/scripts/local-runtime/hithink-news-pagination-admission.mjs plan
node ../daily-stock-analysis/scripts/local-runtime/hithink-news-pagination-admission.mjs apply <计划中的版本> <计划中的摘要>
node ../daily-stock-analysis/scripts/local-runtime/hithink-news-pagination-admission.mjs verify
```

该入口只处理仍在有效期的招商银行 `600036.SH` 既有首屏记录；不续期、不扩大范围，不修改已冻结的研究材料。代码部署继续使用 `sync-code.sh dsa` 或必要的 `update.sh dsa`。分页逐页校验倒序，在全部时间已知、无时序异常且走过所选起点，或来源 `has_more=false` 时结束当前窗口。当前 Desktop 个股抽屉支持「加载更多」；详情或研究材料的单页消费继续表达其单页覆盖边界。升级后的记录由分页入口核验，下述首屏旧版准入流程仅适用于尚未升级的首屏版本。

### 首屏版本的既有准入与续验

标的详情和竞技场研究材料从 `CN/STOCK/NEWS_SEARCH` 读取招商银行（`600036.SH`）最近三天的新闻。正式接入限同花顺个股时间线，每次有效期七天。查看计划、应用并独立回读的方法如下。

```sh
node ../daily-stock-analysis/scripts/local-runtime/hithink-arena-news-admission.mjs plan
node ../daily-stock-analysis/scripts/local-runtime/hithink-arena-news-admission.mjs apply <计划中的版本> <计划中的摘要>
node ../daily-stock-analysis/scripts/local-runtime/hithink-arena-news-admission.mjs verify
```

计划和回读只访问本机配置与证据。首次切换只允许接替已过期的贵州茅台记录，旧范围和证据完整保留。申请同一标的续期时，剩余时间超过一天会复用已有准入。进入最后一天或到期后，重新执行以上步骤，以当前账号的一页、最多十条新闻元数据核验后续期。来源停用、凭据或适配器版本变化、并发更新均应先重新核查。详情显示的“部分覆盖”表示来源仅提供近期首屏。

双端实读需要健康的本机开发 Server、已构建的 Desktop、Edge 和宿主机可导入的 Playwright。在本仓库启动桌面预览，在另一终端运行验收。验收读取一场仅含招商银行的比赛协议，浏览器通过真实详情入口获取一页新闻，随后在目标 Server 用竞技场的读取器获取一页。回执保存在主仓的 `.recovery-artifacts/arena-news/consumers-*/`。

```sh
THESIS_LEDGER_API_URL=http://127.0.0.1:3000 pnpm --dir ../thesis-ledger/apps/desktop exec vite preview --host 127.0.0.1 --port 5197 --strictPort
# 本机 Codex 提供 Playwright 时可从其依赖目录加载
export NODE_PATH="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
node ../thesis-ledger/scripts/local-runtime/verify-hithink-arena-consumers.mjs <招商银行比赛ID>
```

复核研究包时，从主仓执行以下命令，把变量设为验收输出的绝对目录。隔离 PostgreSQL 使用上述回执和受控收盘价，经正式封存及决策输入流程检查两名参赛者是否收到同一份新闻，新增模型与来源请求均为零。

```sh
ARENA_HITHINK_RECEIPT_DIR=<双端回执目录> node scripts/test-journal-review-postgres.mjs test/model-arena/hithink-news-postgres.integration.test.mjs
```

### 使用冻结新闻做即时回放

`node ../thesis-ledger/scripts/local-runtime/model-arena-replay.mjs --check <输入文件>` 核对新闻发布时间、材料可用时间、模型绑定和全员预留计划。盘后材料若使用当日收盘价，须在 `closedSession` 中提供该日已取得的完整日线回执，与事实逐项一致。已收盘且没有调度记录的单日草稿也可用作独立回放。收到该轮模型额度授权后以同一文件执行 `--run`，先独占写入按比赛 ID 保存的全员预留，再次核对全员计划后各派发一次。原始回执写在主仓 `.recovery-artifacts/arena-replays/`。

## 已有个股新闻准入的日期起点扩展

通用入口为 `../daily-stock-analysis/scripts/local-runtime/news-admission.mjs`，必须显式指定一个 `--source`，可选 `akshare-eastmoney`、`hithink-timeline`、`yahoo-search`、`cls-roll` 或 `wallstreetcn-a-stock`。首次新增仍使用来源专属确认与证据，缺少来源时拒绝执行。只有明确授权扩大原范围时，才对原 `600519.SH` 的 `--source=akshare-eastmoney` 或 `--source=hithink-timeline` 使用 `--extend-existing`，并传入 `--expected-record-version=<旧版本>`、`--expected-record-sha256=<完整旧行的SHA-256>`。旧行摘要采用 Python `json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))` 的 UTF-8 字节计算。

仍需提供来源专属的 `--confirm`、正式单页实读得到的 `--feed`、`--sha256`、`--observed-at`、提前后的 `--start`、原 `--end` 和 `--recorded-by`。范围扩展不能传 `--valid-hours`；日期起点最多回看 7 天，原截止日、生效／到期日、来源与凭据版本保持。默认仅生成计划，`--apply` 执行，随后用同一输入及原旧版本／摘要加 `--verify` 只读核验；不同时使用 `--apply` 与 `--verify`。

该入口在原项目锁及 SQLite 写事务内核对精确目标、旧版本和完整摘要、有效期及启用／凭据状态。新证据包含完整旧准入及摘要，原证据文件不覆盖。状态漂移即拒绝且不自动重试；不修改来源启停、政策路由或业务表。

## 研报元数据的长期按需读取

对应主仓的[已完成长期按需任务](../../../thesis-ledger/docs/archive/tasks/2026-10-23-research-report-on-demand.md)与[现行规格](../../../thesis-ledger/docs/specs/2026-10-23-research-report-on-demand.md)。`CN/STOCK/RESEARCH_REPORT_METADATA -> akshare/eastmoney` 使用已有显式政策路由和来源启停判定就绪，当前已支持的全部沪深股票可按需查询；不再绑定固定标的、日期窗口或 24 小时验收记录。一次只读一个目录股票的一页，日期可选，不自动抓取。停用来源或移除该路由即可撤销后续读取。

Server 的 `POST /api/market-data/research-report-metadata/collect` 接收已有 `instrumentId` 及有界分页和请求预算；省略 `start`、`end` 时不补隐藏筛选，来源适配器将协议必需的 `beginTime/endTime` 传空字符串。新响应为 `research-report-metadata-v2`，`historyComplete=false`；旧 v1 快照仍可通过本地历史入口读取。无日期筛选不代表上游提供全部历史。

保留数据的正式完整更新及目标验收已通过：`600519.SH` 无日期、第 1 页/2 条/3000 ms 采集返回 201，取得 2 条、来源总数 772；新旧历史读取 200，旧快照摘要保持。原来源启停、20 条路由、11 张受保护业务表完整摘要及全部挂载一致，5 个服务健康；用户确认的预期并发修改保留。[完整证据](../../../thesis-ledger/docs/evidence/2026-10-23-research-report-on-demand.md)记录镜像、时间与保护范围。

### 首次临时验收记录（历史）

[首次验收任务](../../../thesis-ledger/docs/archive/tasks/2026-10-23-research-report-metadata.md)曾使用下述独立维护入口，默认仅输出计划。其范围和有效期只属于当时的验收证据，脚本与证据保留，不用于当前业务启用或日常续期：

```bash
node ../daily-stock-analysis/scripts/local-runtime/report-metadata-admission.mjs \
  --symbol=600519.SH --start=2026-08-01 --end=2026-10-23 \
  --confirm=thesis-ledger-dev/akshare/eastmoney/600519.SH/2026-08-01/2026-10-23
```

当时通过 `--collect` 进行第 1 页、2 条、3000 ms 的标准绑定读取，在原数据卷保存最小证据；随后显式应用 24 小时的首次准入并只读核验。这些操作已完成，长期使用无需重复执行。

历史应用要求 30 分钟内的同版本证据及首次写入，不允许覆盖或续期，也不初始化数据库。原记录和 SHA-256 证据继续保留；它到期后不影响新的长期按需读取。原业务路由和来源启停保持不变。

定向检查：

```bash
node --test ../daily-stock-analysis/scripts/local-runtime/report-metadata-admission.test.mjs
PYTHONPATH=../daily-stock-analysis ../daily-stock-analysis/.venv/bin/python -B ../daily-stock-analysis/scripts/local-runtime/report-metadata-admission.test.py
```

### Server 重启留存验收

`node ../thesis-ledger/scripts/local-runtime/restart-server-for-acceptance.mjs --confirm=thesis-ledger-dev/thesis-ledger --image=<当前完整镜像ID>` 默认只输出计划。添加 `--apply` 后，正式入口锁定本地项目并核对镜像、容器及全部挂载，只重启同一健康 Server 容器，等待最多 60 秒恢复健康；不构建、不更新代码、不操作 Worker 或 DSA，不重试失败的重启。应在业务快照建立并取得摘要后执行，随后通过本地历史接口核对留存。定向检查为 `node --test ../thesis-ledger/scripts/local-runtime/restart-server-for-acceptance.test.mjs`。


## 账号等级隔离验收

账号等级改动的目标隔离验收使用 `node ../daily-stock-analysis/scripts/local-runtime/tushare-account-level-acceptance.mjs <私有维护目录中的新报告绝对路径>`。入口固定已运行的本项目 DSA，在其 `/tmp` 下创建合成 SQLite，以两个独立 Python 进程验证 120 分的范围内检查、权限落盘和恢复；出站连接被阻止，临时目录在结束时清理。它不读取或改写正式账号凭据、配置及业务数据库，也不替代实际 Server API 和更新前后数据留存核对。

## 构建配置

本仓 Dockerfile 定义 `RUST_IMAGE`、`PYTHON_IMAGE`、`DEBIAN_MIRROR` 构建参数以及 `longbridge_upstream` 固定 SDK 源上下文。独立构建从固定上游版本获取源码；infra 的正式入口在停机前调用 `scripts/prepare-local-sdk-source.sh <绝对工作目录> <绝对缓存目录>`，再传入经校验的缓存源。该命令不操作 Docker 或业务数据库，缺少 Python 时直接失败。
