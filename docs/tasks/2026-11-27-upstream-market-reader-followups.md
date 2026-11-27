# 行情读取兼容与来源校验任务

规格见[行情读取兼容与来源校验](../specs/2026-11-27-upstream-market-reader-followups.md)。

- [x] Yahoo 兼容日线和报价回归（AC1）。
- [x] efinance 批量缓存与单标快照回归（AC2）。
- [x] TickFlow 批量验证、凭证隔离和缓存回归（AC3）。
- [x] 富途本地连接实验、AkShare 调用盘点和 TickPlus 接入核对（AC4）。
- [x] 用当前凭证运行 TickFlow 逐功能权限检查，并验证报价拆批。
- [x] TickFlow ETF 精确报价接入，并通过 `510300.SH` 真实 DSA API 验收（AC5）。
- [x] TickFlow ETF 不复权日线接入，并通过 `510300.SH` 真实 DSA API 验收（AC6）。

## 本地验证

- 通过 327 项定向回归，覆盖具名报价、日线、逐功能权限、批量缓存及 AkShare 历史超时。补跑的 efinance 与 Tushare 账号权限回归通过 34 项，包含重叠用例以及 ETF 批次内单行价格无效的新增用例。Python 语法、关键 lint、数据边界和确定性检查通过。
- TickFlow 用受控回包验证两类凭证容量、批量请求被拒后的缩小重试、凭证更换和自定义端点隔离。每轮沿用 14 次逐功能请求预算。
- 富途回环实验中，对端接收 TCP 连接但不应答，worker 在 4.51 秒返回 `timeout`。独立回归验证下一次报价请求可成功。AkShare 保留现有日线隔离和超时，具名数据服务当前没有全市场统计调用。

## TickFlow 真实凭证与 ETF 验收

2026-11-27 13:29 至 13:31（中国标准时间）。只读确认当前环境凭证与运行中 DSA 保存的 TickFlow 凭证一致。以本工作树代码、临时控制库运行正式 `POST /api/v1/thesis-ledger/control/providers/tickflow/test`（`purpose=account_access`），请求编号前缀为 `tickflow-live-20261127-cc694b`。凭证未写入验收文档，临时库已清理。

| 权限检查轮次 | 报价试样数 | TickFlow HTTP | 读取端采用容量 |
| --- | ---: | ---: | ---: |
| 1 | 1 | 200 | 1 |
| 2 | 2 | 200 | 2 |
| 3 | 4 | 200 | 4 |
| 4 | 8 | 400 | 4 |
| 5 | 6 | 400 | 4 |
| 6 | 5 | 200 | 5 |

- 每轮 14 次上游请求。股票报价及三种股票日线口径保持 `allowed`；股票和 ETF 分钟线共 10 项返回 HTTP 403，权限检查记为 `permission_missing`。报价容量根据回包收敛到当前凭证已验证的 5 个。用 6 个股票标的走实际报价预取时，发出 5+1 两个请求，均返回 HTTP 200，6 个有效结果全部进入缓存。
- **ETF 精确报价**。沪深 300 ETF（`510300.SH`）在账号端点返回 HTTP 200，响应标的匹配，价格 4.104，来源时间为 2026-11-27 13:30:51。DSA 的 `CN/ETF/REALTIME_QUOTE` 精确目录无 TickFlow 绑定，直接调用精确分发返回 `unsupported_capability`，没有发出上游请求。
- **ETF 精确日线**。同一标的在免凭证日线端点返回 HTTP 200，2026-11-23 至 2026-11-25 不复权窗口取得 3 根日线，日期有序且 OHLC 有效，末日收盘价 3.97。紧凑回包未回显证券代码，身份依据为请求参数。DSA 的 `CN/ETF/DAILY_BAR/1d/none` 精确目录无 TickFlow 绑定，直接调用精确分发返回 `unsupported_capability`，没有发出上游请求。
- 共发出 88 次 TickFlow 上游请求，HTTP 200 共 26 次，403 共 60 次，400 共 2 次，未遇限频。运行中 DSA 的 TickFlow ETF 目录也只登记了分钟线。真实报价容量记录保存在本次临时库内，运行中 DSA 的配置和权限报告未改动。

## TickFlow ETF 接入后验收

2026-11-27 14:42（中国标准时间）。用本工作树的正式 DSA API 和临时控制库，分别将报价及不复权日线路由指向唯一目标 `tickflow/tickflow`。临时策略版本为 1，两项执行资格均通过。

- **精确报价**。调用 `GET /api/v1/thesis-ledger/market/quote`，请求编号 `tickflow-etf-18cf9e20-quote`。HTTP 200，标的 `510300.SH`，最新价 4.140，TickFlow 来源时间 14:42:15，`freshness=live`、`fallbackUsed=false`。成交量原值 27,552,264，成交额原值 11,236,748,700，保留单位未知。首次上游连接超时，SDK 重试取得同来源成功响应。
- **精确日线**。调用 `POST /api/v1/thesis-ledger/market/bars`，固定路由索引 0，请求编号 `tickflow-etf-a159a080-daily`。免凭证端点先返回 `510300.SH/CN/SH/etf` 的当前证券元数据，再返回 2026-11-23 至 2026-11-25 的 3 根不复权日线。DSA HTTP 200，独立交易日覆盖完整，末日收盘价 3.970，单页读取完成。K 线回包身份依请求绑定，当前元数据与历史时点身份分开记录。首次尝试在元数据成功后触及验收脚本共用的请求预算，独立重跑以 2 次匿名上游请求通过。
- 权限检查新增 ETF 报价单标及 ETF 不复权日线两个独立探测，单轮上限由 14 增至 16，总时限仍为 18 秒。股票报价容量仍由股票样本的权限检查确定。
- 一致性复核通过。195 项定向回归覆盖现有股票能力、ETF 身份校验、权限、数据目录和显式路由，语法、关键 lint 和数据边界检查均通过。
- 上述结果来自隔离工作树与真实 TickFlow。目标容器证据见下节。

## 合入、部署与目标验收

- **合入**。Tushare 修复 `65127177` 和行情来源批次 `17d6738f` 快进合入 DSA 的 `yzin`，已推送 `origin/yzin`。为在无 PR 的情况下执行完整门禁，`e411bfb7` 给原 CI 增加手动入口。
- **部署**。2026-11-27 14:49（中国标准时间），在 `/Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra` 执行 `DSA_SOURCE_DIR=/Users/yzin/code/thesis-ledger-workspace/daily-stock-analysis ./scripts/sync-code.sh dsa`，退出码 0。DSA 容器 `8b9686a0c319` 重启后为 `healthy`，镜像 ID 仍为 `sha256:5b2ab24cd84cbf3dab6e5158f131b6b1087ef4e7300e7e1353753de50f7aa27e`。容器内 TickFlow 报价、免凭证日线、权限检查、Tushare HTTP、efinance、Yahoo、富途共 7 个源码文件的 SHA-256 与宿主一致。快更内容位于容器可写层。
- **目标 ETF 精确报价**。调用部署服务的 `POST /api/v1/thesis-ledger/control/providers/tickflow/test`，使用当前凭证的只读诊断，指定 `CN/ETF/REALTIME_QUOTE` 和 `tickflow/tickflow`。请求 `target-tickflow-etf-quote-2b3cedcbdb` 返回 HTTP 200、`ready`、`healthy`、`attempted=true`，样本 `510300.SH`，耗时 4891 毫秒。验收前后核对实际策略版本 203、路由及账号权限报告一致。业务策略的 ETF 报价仍由原目标提供。
- **目标 ETF 不复权日线**。在 DSA 仓根目录执行 `./scripts/local-runtime/probe-backtest-daily-sources.sh --symbol 510300.SH --asset ETF --adjustment none --start 2026-11-23 --end 2026-11-25 --target tickflow/tickflow`。目标容器用临时控制库隔离选路，读取和校验 3 根、覆盖 3 个交易日、单页完成、耗时 2376 毫秒，成交量和金额单位保持未知，指纹为 `064205d023db199613c1d0abbd060a8c14ddbc67526715875234650b06d10dec`。首轮因真实控制状态指纹在诊断期间发生变化退出 1，独立重跑退出 0，报告 `productionControlUnchanged=true`。业务日线路由仍由原目标提供。
- **远端 CI 首轮**。手动门禁在 `e411bfb7` 上发现 Runner 的 DSA 检出目录名与同级仓库约定不同，另有测试分片缺少主仓 Schema。通过 `4b3e3ffd` 修复同级布局和各分片的 Schema 来源，通过 `4a30fcfb` 将部署测试的健康失败断言与现行 infra 错误对齐。[首轮 GitHub Actions](https://github.com/yzin-17/daily_stock_analysis/actions/runs/45954529827)
- **远端 CI 复验**。执行提交为 `4a30fcfb`，2026-11-27 15:12:54（中国标准时间）完成。结论 `failure`。输入检查、`ai-governance`、`docker-build` 与镜像 smoke 通过，3 个后端分片和汇总 `backend-gate` 失败。[复验 GitHub Actions](https://github.com/yzin-17/daily_stock_analysis/actions/runs/45955026148)
- **复验发现**。第 1 个分片在旧报告元数据版本测试失败。第 2、3 个分片报告指标指纹、Yahoo 新闻、AkShare 新闻模块、Longbridge SDK 和 Tushare 跨仓测试缺少 `rtk` 等失败，相关实现或依赖配置沿用合入前基线。与本批直接相关的 ETF 绑定数量、目录夹具和 CI 手动入口断言由 `eb2f2165`、`608e2fd2` 修正并推送，45 项本地定向回归通过。两次后续提交仅改测试，部署源码与远端 CI 所测的源码一致。

## 待独立验收

- TickPlus 的公开 HTTPS 行情地址在本地代理下未完成握手，公开 SDK 的行情 HTTPS 能力和接口命名仍需核实。保持独立来源待准入状态。
