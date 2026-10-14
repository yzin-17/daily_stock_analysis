# ThesisLedger 公告现金分红来源

## 当前范围

`OfficialNoticeDividendProvider.read(symbol, start, end, data_as_of)` 读取仓库内已核验的来源包。首个包是 `510300.SH` 的 `2023-01-03..2023-02-01`。已有精确 V3 事件库存、Control manifest 与 Data Token 鉴权 HTTP 接线；当前 RouteAdmission 的来源修订和证据摘要必须绑定该包原字节，不自动签发准入。主仓入账/目标与页面消费继续独立验收。

管理人分红公告确认每 10 份 0.6400 元、登记日 2023-01-13、除息日 2023-01-16、现金发放日 2023-01-19。2023 年年报第 52 页的利润分配表列出本期唯一现金分配，并确认金额与登记/除息日。包中的完整覆盖仅针对这一有界窗口的现金事件，不涵盖拆分、上市以来全历史或历史当时版本。

## 数据与时钟

- 金额使用 `Decimal` 按明确的每十份单位换算为每份 `0.064 CNY`，不用浮点数。来源金额/单位与标准化每份金额分别保留。
- 查询按明确 `effectiveDate` 过滤；登记、除息、发放日互不替代。`occurredAt` 采用既有事件合同的除息日零点标识，不声称公告提供了盘中时刻。
- 公告发布日期仅在来源文档保存；`availableAt` 是公告与覆盖年报都已实际核验的最晚时刻。`fetchedAt` 是本次本地读取时刻，二者不混用。首版不生成 `strategyVisibility`，历史可见性为 `notVerified`。
- `providerRevision` 使用来源包原字节 SHA-256，同包跨读取时刻保持事实身份；数据、日期、来源引用、摘要或空白改变均生成新修订。返回原件 HTTPS 地址、SHA-256、核验页码和时刻，运行时只读本地包、不下载或重新解析 PDF。
- 内部观察的 `coverage.complete=true` 只表达已审查包中的现金分配覆盖；它不是 V3 响应，不包含 `admission`，不能直接授予网关准入或绕过 Snapshot 检查。接入时须将精确来源包修订和原文绑定到现有鉴权/冻结合同。

证券/窗口未覆盖、冻结截点早于实际核验分别返回 `not_covered`、`insufficient_coverage`、`not_available`。非法请求返回 `invalid_request`，缺文件返回 `upstream_failure`。币种/单位、日期次序、重复事件或 JSON 字段、来源文档、年报依据、独立事件清单不一致时返回 `invalid_response`，不返回伪造空集。

## 来源与验证

生产输入：`data_provider/cash_distributions/510300-2023-01.json`。公开原件：[分红公告](https://static.cninfo.com.cn/finalpage/2023-01-09/1215552905.PDF)、[2023 年年报](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2024-03-30/510300_20240330_437H.pdf)。本次实际核验时刻为 2026-10-07 02:31（北京时间）；原件摘要记录在来源包，原 PDF 未作为仓库二进制产物加入。

定向验证：`.venv/bin/python -m pytest tests/test_thesis_ledger_notice_dividends.py tests/test_thesis_ledger_notice_events_v3.py`。来源身份为 `CN/ETF/CASH_DISTRIBUTION`、`official-dividends/cn-etf-cash-notices`；现有 `/api/v3/thesis-ledger/market/events` 端点固定策略/目录修订和来源序号，读取前后复核。包变化、撤销、来源/窗口不匹配或安全状态变化后拒绝晚到结果；响应保存 `noticeDividendEvidence` 原文，主仓在线与离线重新核验原摘要及逐事件金额/日期。该来源没有 `SPLIT_EVENT` 库存，不扩展旧 HiThink 数字进度语义。此读取器不对用户账户入账；现金执行模型和正式目标闭环由主仓 M31-r3～r5 维护。
