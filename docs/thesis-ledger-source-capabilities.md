# ThesisLedger 有限来源能力目录

> 盘点日期：2026-09-25。输入基线：DSA `f497b6da`（盘点时工作区干净）、主仓 `fe0e871e`。本目录是代码、manifest 与现有文档的静态盘点，不代表账号已经授权、接口已经探测或目标运行态已经通过。

## 2026-10-03 当前基础能力

| 精确来源 | 资产 / 周期 | 可用价格口径 | 启用条件 |
| --- | --- | --- | --- |
| `hithink/fund-market-historical` | CN ETF / 1d | qfq | 已配置 API Key、来源启用、精确路由保存 |
| `tencent/tencent` | CN ETF / 1d | none、qfq、hfq | 来源启用、精确路由保存 |

上述四个条目取消人工窗口准入前置，日级状态从实际行情自动整理。HiThink qfq、腾讯 qfq/hfq 的真实普通回测和重放已验证，同口径整窗备用用于图表和固定快照归一化价格研究。原生量额语义未知时保持未知，只限制使用相应字段的规则。股票、实时报价、公司行动、严格 PIT 及下文其他能力分别按实际状态开放；历史静态清单不能覆盖本节当前结论。证据见[主仓当前验收](../../thesis-ledger/docs/tasks/evidence/2026-10-03-common-price-baseline.md)。

## 1. 判定口径

- 一个登记单元由 `Provider × 实际上游 × 资产类型 × 能力 × 周期/口径` 确定。周期、复权或数据可见时间没有源码依据时，写“未知”，不从库名、字段名或其他市场推断。
- “代码/manifest 声明”只表示仓库存在适配入口或静态能力声明；“真实可用”需要相应 G0 的授权只读探针。2026-09-25 静态盘点没有读取运行时凭据、发送 Provider 请求、下载 SDK 或查询外部服务；后续增量证据仅适用于各自记录的账号、标的和窗口，不能推定所有账号的授权、套餐、配额与覆盖。
- manifest 的 `requiresCredential`、`configurationMode`、`markets` 和能力集合是代码声明，不是用户账号权限或实际响应证据。内置/免凭据也不等于有数据使用授权。
- `Provider ID`、SDK/封装名、`upstreamSource` 和实际 HTTP/API 上游是不同概念。AKShare 与 Efinance 的 EastMoney 接口不能作为两个独立备用；同一来源的两个包装库也不能仅凭包装名算独立源。当前 `sourceId=akshare` 等 Provider 级条目不总能唯一定位到底层 endpoint。
- “原生”表示由来源接口返回；“派生”表示 DSA 本地换算、聚合或日历库结果。具体调整算法、锚点、量价单位、历史修订方式未证明时分别标未知。
- 现有消费者分为 DSA 原生分析链路与 ThesisLedger Control/Data Contract 路由。二者不能互相证明真实准入；Consumer 的契约能力也不等于所有当前 Provider 都可执行。

## 2. Spec 来源范围与候选选择行

本节把 Spec §4.1 的来源目标拆成单资产、单能力的候选行。这里是需求候选，不代表 DSA 已有适配；标为“待选”的行因 endpoint、资产类型或字段粒度未知，不能派发为可执行单元。相同 SDK/封装名不代表不同实际上游。已有腾讯、Efinance、BaoStock 能力以 §3/§4 的代码登记为准。

| Provider / 候选来源 | 实际上游 / endpoint | 单一资产类型 | 单一能力 | 周期 / 口径 | 状态、Spec 范围与缺口 |
| --- | --- | --- | --- | --- | --- |
| HiThink Financial-API | HiThink API；具体 endpoint 未知 | STOCK | DAILY_BAR | none | Spec 候选；接口、窗口和授权未探测，不可执行 |
| HiThink Financial-API | 同上，保持同一来源关系 | STOCK | DAILY_BAR | forward | Spec 候选；基准与响应标记待 G0-H |
| HiThink Financial-API | 同上，保持同一来源关系 | STOCK | DAILY_BAR | backward | Spec 候选；基准与响应标记待 G0-H |
| HiThink Financial-API | 同上，保持同一来源关系 | ETF | DAILY_BAR | forward；adjust:null 仍按前复权 | Spec 候选；单次窗口最长五年，不代表总历史仅五年；待 G0-H |
| HiThink Financial-API | `GET /api/a-share/prices/snapshot?thscodes=<单只完整代码>` | STOCK | REALTIME_QUOTE | 最新快照；上游时间可空 | R02.1 已选择现有 Quote Reader；单标适配器本地存在，生产执行接线、真实时点/权限与准入待 R02.2/G0-H |
| HiThink Financial-API | `GET /api/fund/market/snapshot?thscode=<单只完整代码>`；与股票同一 HiThink 服务 | ETF | REALTIME_QUOTE | ETF/LOF 场内最新快照；本叶只选 ETF | R02.3 已选择现有 Quote Reader；单标适配器本地存在，ETF 原生量额单位、生产执行接线与真实准入待 R02.4/G0-H |
| HiThink Financial-API | HiThink API；分红 endpoint 未知 | MUTUAL_FUND | CASH_DISTRIBUTION | 事件日期字段未知 | Spec 候选；具体事件类型和日期语义待 G0-H |
| HiThink Financial-API | HiThink API；研究数据 endpoint 未知 | 资产待选 | RESEARCH_DATA（字段未定） | 粒度、可见时间未知 | 来源选择行；资产/字段/Consumer 未定，不可执行 |
| AKShare / 现有 EastMoney 适配 | 实际 endpoint 待按函数确认 | STOCK | DAILY_BAR | none | Spec 目标口径；只表示保留目标，不证明每条现有路由支持 |
| AKShare / 现有 EastMoney 适配 | 同一候选来源关系 | STOCK | DAILY_BAR | qfq | 同上；复权锚点及历史修订待核 |
| AKShare / 现有 EastMoney 适配 | 同一候选来源关系 | STOCK | DAILY_BAR | hfq | 同上；复权锚点及历史修订待核 |
| AKShare / 现有 EastMoney 适配 | 实际 endpoint 待按函数确认 | ETF | DAILY_BAR | none | Spec 目标候选；实际 endpoint/支持范围待核 |
| AKShare / 现有 EastMoney 适配 | 同一候选来源关系 | ETF | DAILY_BAR | qfq | Spec 目标候选；不得把折算日直接当除权生效日 |
| AKShare / 现有 EastMoney 适配 | 同一候选来源关系 | ETF | DAILY_BAR | hfq | Spec 目标候选；实际支持范围待核 |
| AKShare | fund_cf_em 等；真实 HTTP 上游/字段仍待确认 | ETF | SPLIT_EVENT | 折算日与交易生效日关系未知 | Spec 要求候选；R07.1；接口数据不自动等于标准事件事实 |
| AKShare | 分红接口待确认 | ETF | CASH_DISTRIBUTION | 事件日期/现金单位未知 | Spec 要求候选；R07.1、G0-M |
| AKShare | 公告索引 endpoint 待确认 | ETF | CORPORATE_ACTION_NOTICE | 公告日、可见日未知 | Spec 要求候选；公告索引不等于标准事件事实 |
| Tushare Pro | Tushare API；ETF 日线 endpoint 待核 | ETF | DAILY_BAR | 1d；adjustment 未知 | Spec 候选；日线成交量/金额单位及账号接口权限须分别核实 |
| Tushare Pro | fund_adj；已实现精确原始读取 | CN ETF | ADJUSTMENT_FACTOR | 按 trade_date；因子锚点及首次可见/历史修订未知 | M25-a 本地实现完成；冻结消费、转换与真实准入未完成，因子不等于事件表 |
| Tushare Pro | fund_div；精确读取/结构化标准化已实现 | MUTUAL_FUND（领域 NAV_FUND） | CASH_DISTRIBUTION | 独立公告/实施公告/登记/除息/支付日期；真实观测时间保留 | M26-a/b1 本地通过；分页/历史覆盖、冻结消费与真实权限待验收 |
| Tushare Pro | fund_div；不将 OF 与交易所代码隐式互换 | CN ETF | CASH_DISTRIBUTION | 按除息日选窗，实施记录形成现金事实 | M26-b2 同完整ASCII代码/独立分红币种原文、受控读取及事件V3库存/HTTP定向通过；Server原字节在线/离线消费已验证，URL authority/主机名差异已窄修复；DSA完整离线门禁未通过，目标同步跳过，真实原文真实性、权限及完整历史覆盖仍未通过 |
| 官方 TdxAiData | 官方 SDK/API 尚未确认 | CN STOCK | DAILY_BAR | none | 来源选择候选；SDK、授权、系统依赖未核实 |
| 官方 TdxAiData | 同一协议候选，口径支持未知 | CN STOCK | DAILY_BAR | qfq | 来源选择候选；需确认精确基准及返回标记 |
| 官方 TdxAiData | 同一协议候选，口径支持未知 | CN STOCK | DAILY_BAR | hfq | 来源选择候选；Spec 提醒后复权可能依查询窗口而变 |
| 官方 TdxAiData | 官方分钟 endpoint 未知 | CN STOCK | INTRADAY_BAR | interval、窗口、复权未知 | 来源选择候选；不得扩大 M1 日线验收边界 |
| 官方 TdxAiData | 官方权息 endpoint 未知 | 资产待选 | CORPORATE_ACTION / RIGHTS | 事件种类、映射、日期未知 | 来源选择行；资产和 endpoint 未定，不可执行 |
| RQData | RQData API；拆分 endpoint/覆盖资产待核 | 资产待选 | SPLIT_EVENT | 除权日与比例字段；可见时间未知 | 来源选择行；股票/ETF 归属未由 Spec 固定，不可执行 |
| RQData | RQData API；分红 endpoint/覆盖资产待核 | 资产待选 | CASH_DISTRIBUTION | 登记、除息、派息日期字段；可见时间未知 | 来源选择行；资产/权限未固定，不可执行 |
| RQData | 官方通用 `get_price`；场内基金支持历史日行情 | CN ETF 候选 | HISTORICAL_BAR/1d/none 候选 | 显式窗口与不复权参数已核；原生量额单位、身份、修订及历史覆盖未知 | R02.17 单一候选；[核对证据](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-17-rqdata-etf-bar-selection.md)。事件权限不证明行情权限，未准入 |
| a-stock-data v3.10.0 候选代码 | 固定提交 `2e0ae63`；腾讯 `qt.gtimg.cn/q=` 报价和 `fqkline/get` 日 K | CN STOCK 报价；日 K 为同源备选 | REALTIME_QUOTE；DAILY_BAR/none、qfq | 当前报价、日 K；来源可见时间与原生单位未审 | 既有 AKShare 腾讯报价和 TencentFetcher 日 K 已走同源；R07.14 选择不新增 Provider，R07.15 暂不接入；[G0-R 审查](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-g0-r-auxiliary-source-audit.md) |
| a-stock-data v3.10.0 候选代码 | 固定提交 `2e0ae63`；通达信官网 `g4day/{ymd}.zip` | 沪深北全市场集合 | POST_MARKET_PACKAGE；单日快照 | 原码声明量为股、额为元；覆盖与发布时间未独立核验 | 实际 Provider 为通达信官网文件；缺已选现存 Consumer，R07.16/17 暂停，G0-R 未过 |
| a-stock-data v3.10.0 候选代码 | 固定提交 `2e0ae63`；巨潮 `new/hisAnnouncement/query` | CN STOCK | ANNOUNCEMENT_INDEX | 输出公告日，精确发布时间/修订/分页完整性未知 | 现有筛选上下文已经 AKShare 读取巨潮；同源不重复准入，R07.18/19 暂停 |
| free-stockdb | 上游文档不内置数据地址；镜像位置和原始数据来源未知 | 资产待选 | 能力待选 | 周期、口径、覆盖未知 | R08.1 unavailable；无可信镜像/许可/可达性，Reader/Provider 保持关闭；[G0-R 审查](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-g0-r-auxiliary-source-audit.md) |
| 问财等 Skill 底层检索 | 第三方固定版声称 `openapi.iwencai.com` + SkillHub 2.0；官方合同未独立核实 | 资产待选 | RESEARCH_RETRIEVAL | 字段及时间口径未知 | R07.20/21 待验证 Key、许可、字段及 Consumer；自然语言结果不作为确定性行情事实 |
| 问财等 Skill 底层检索 | 同一候选服务，公告原发布源身份待确认 | 资产待选 | ANNOUNCEMENT_LOOKUP | 公告来源、可见时间未知 | R07.22/23 待验证；未核底层服务/许可，不可执行 |

Spec §4.2 的五类能力范围（行情、基金、事件、基础、研究）均由候选行或 §3/§4 已有能力行承接。2026-09-25 盘点基线未发现 HiThink、RQData、官方 TdxAiData、a-stock-data、free-stockdb 或问财底层检索的已装 SDK/Provider 适配；后续已实现子项以增量证据及 §5 当前状态为准；Pytdx 是独立社区行情服务器协议。Spec §14.1 的四项外部前提也在目录中保留为准入门：HiThink 的 Key/标的/复权响应见本节及 R02/R03/R05/R07；raw/hfq 可用来源按 §3 中明确的口径声明逐源探测，当前没有真实账号通过证据；官方 TdxAiData 的 SDK/授权/系统兼容仍是选择项；free-stockdb 镜像与许可仍是选择项。

2026-09-27 增量：RQData 的 `rqdatac 3.7.1` 与基金扩展已安装并通过目标禁网导入验证；`fund.get_split`/`fund.get_dividend` 有精确标准化、spawn 期限隔离及凭据修订读取接缝。`rqdata` 已注册到 Control 的加密 `username_password` 配置入口，配置完成仅证明账号已保存，可路由能力保持为空。证券映射、币种、接口权限、历史覆盖及事件 V3 准入仍未完成；盘点基线的“未安装”描述不再代表这些已实现子项的当前状态。

2026-09-28 增量：HiThink ETF `fund-market-historical` 的 Control V3 生效策略现在消费内部精确 RouteAdmission，并与 Data V3 共用适配、来源及环境凭据 HMAC 修订核对；配置了 Key 但无有效准入时仍为 `not_admitted`。目标 `159516.SZ` 的含预热窗口 68 日已与 Sina 未复权日线逐日核对，又与深交所同窗官方日线和 2025 年 7 月月报独立核对量额单位（主仓 `CONT-G0-H-159516-szse-units`）。据此 DSA 仅在该标的 `2026-04-30..2026-08-09` 内、当前 ETF 历史来源修订下，把原生 `volume/amount` 声明为 `fund-unit/CNY`；其他标的、窗口和修订继续为 `unknown`，数值不转换。该字段声明不签发 RouteAdmission，不证明前复权算法或历史可见性。官方 `sync-code.sh dsa` 已将本次源码同步到目标容器可写层，容器健康且精确目录仍 `not_admitted`；镜像未更新，容器重建后快更会消失。V1/V2 HiThink 路由继续拒绝。

身份合同增量：已增加当前准入摘要绑定的 ETF 直接代码映射、独立分红币种及读取前后复核接缝，返回保留原文，见 [RQData 身份证据说明](thesis-ledger-rqdata-identity-evidence.md)。主仓共享事件 V3 原文合同与 Server 在线/离线校验已完成；Schemas 367 项、Server 相关 57 项及实际 Parquet 验证通过。DSA 精确 cash/split 库存与生产 HTTP 已接线，新增 11 项、相关 166 项及跨仓两份 exchange 校验通过；官方同步后六份源码摘要一致，目标拒绝路径通过且 healthy。真实证券映射、币种、账号权限、完整历史和目标正向请求仍未通过。

## 3. ThesisLedger Control manifest 原子登记单元

本节把当前代码登记的 Provider × manifest source ID 组合展开为原子行。manifest 的 Provider/source ID 是代码路由声明，不总是实际 HTTP/API 上游；上游或 endpoint 未知的行明确保留“未知”，不能视为已可执行或独立备源。每行只登记一个资产、一个能力和一个周期/口径。Contract V1 的 DAILY_BAR 仅接受 1d；manifest 未登记的能力不从 DSA native 方法外推。

| Provider / manifest source ID | 实际上游 / endpoint | 单一资产 | 单一能力 | 周期 / 口径 | 代码性质与凭据声明 | Consumer、缺口与 G0 |
| --- | --- | --- | --- | --- | --- | --- |
| akshare / akshare | Provider dispatch；实际 endpoint 未知 | STOCK | DAILY_BAR | 1d / none | 来源数据原生，字段映射由 DSA 派生；不要求凭据 | ProviderRuntime 与 native 日线路由；sourceId 是候选选择项，实际 endpoint/基准待 G0-M |
| akshare / akshare | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / qfq | legacy 默认 qfq；调整参数来自 API 请求 | 同上；价格锚点、修订、窗口待 G0-M |
| akshare / akshare | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / hfq | 精确适配器接受 hfq | 同上；价格锚点、修订、窗口待 G0-M |
| akshare / akshare | Provider dispatch；实际 endpoint 未知 | ETF | DAILY_BAR | 1d / none | 来源数据原生，字段映射由 DSA 派生；不要求凭据 | Runtime 与 native 日线路由；ETF 单标和窗口待 G0-M |
| akshare / akshare | 同上，保持同一来源关系 | ETF | DAILY_BAR | 1d / qfq | 精确适配器接受 qfq | 同上；价格锚点、修订、窗口待 G0-M |
| akshare / akshare | 同上，保持同一来源关系 | ETF | DAILY_BAR | 1d / hfq | 精确适配器接受 hfq | 同上；价格锚点、修订、窗口待 G0-M |
| akshare / akshare | 旧路由 alias；当前默认 adapter 调用东财 stock_zh_a_spot_em | STOCK | REALTIME_QUOTE | 采样频率、时点未知 | 上游行情经 DSA 清洗；不要求凭据 | 与新 `akshare/eastmoney` 共用健康/熔断；V1 无 source 为新浪，不合并。旧路由不改写；字段、单位、时点及权限待 G0-M |
| akshare / akshare | 同上，保持同一来源关系 | ETF | REALTIME_QUOTE | 采样频率、时点未知 | 上游行情经 DSA 清洗；不要求凭据 | ProviderRuntime/native quote；ETF 覆盖、字段和权限待 G0-M |
| akshare / eastmoney | EastMoney 经 AKShare；stock_zh_a_spot_em | STOCK | REALTIME_QUOTE | 采样频率、时点未知 | adapter 使用 `em` 参数，执行身份仍为路由键 `eastmoney`；不要求凭据 | 新建默认 Desired Policy；旧 `akshare` alias 的最近 open 阻断本路由，后续健康只写规范键；与 Efinance 同源不算独立备用，G0-M 待验 |
| akshare / eastmoney | 与旧 alias 同一 `fund_open_fund_info_em` Reader | MUTUAL_FUND | FUND_NAV | 净值日不是披露时刻；正式性未知 | 新建默认 RouteTarget，Runtime 从历史序列选择最新 | 旧/新/V1 无 source 最近 open 在本能力内共用健康与熔断；修订/披露时间及 G0-M 待验 |
| akshare / eastmoney | 与旧 alias 同一 `fund_open_fund_info_em` Reader | MUTUAL_FUND | FUND_NAV_HISTORY | 日期序列，来源可见时间未知 | 新建默认 RouteTarget，Reader 独立于正式净值健康组 | 旧/新/V1 无 source 最近 open 在本能力内共用健康与熔断；完整覆盖/修订及 G0-M 待验 |
| akshare / akshare | 旧路由 alias；当前 adapter 的 fund_open_fund_info_em 实际上游为 EastMoney | MUTUAL_FUND | FUND_NAV | 净值日不是披露时刻；正式性未知 | ProviderRuntime 从同一历史序列选择最新；不要求凭据 | 新默认改用 `akshare/eastmoney`；旧/新/V1 在本能力内共用健康与熔断，旧路由不改写；正式净值时间/修订待 G0-M |
| akshare / akshare | 旧路由 alias；AKShare fund_open_fund_info_em 经 EastMoney `pingzhongdata` | MUTUAL_FUND | FUND_NAV_HISTORY | 净值日期序列；更新/可见时间未知 | 上游净值，DSA 做字段/日期标准化；不要求凭据 | AkshareFetcher.get_fund_nav_history → NAV History Reader；旧/新/V1 在本能力内共用健康与熔断，不是盘中估值，缺失/修订待核 |
| akshare / akshare | AKShare fund_portfolio_hold_em；HTTP 上游未核 | MUTUAL_FUND | FUND_HOLDINGS | 报告期快照；披露时间未知 | Provider 返回快照，DSA 选择当年/上一年并映射；不要求凭据 | AkshareFetcher.get_fund_holdings → Holdings Reader；报告期、空/无权限映射待 G0-M |
| akshare / akshare | Chip endpoint 未知 | STOCK | CHIP_SUMMARY | 快照日、窗口和单位未知 | Provider 数据经 DSA 字段处理；不要求凭据 | ThesisLedger/native 研究入口；字段、时点和覆盖待 G0-M |
| akshare / eastmoney | EastMoney 经 AKShare；stock_zh_a_hist | STOCK | DAILY_BAR | 1d / none | 上游 Bar 原生，列名/指标由 DSA 派生；不要求凭据 | 日线路由与回测 Reader；请求上游与 EastMoney 直连/其他包装同源计数待核 |
| akshare / eastmoney | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / qfq | 适配器接受 qfq | 锚点、历史修订、窗口待 G0-M；不得仅按包装名算独立源 |
| akshare / eastmoney | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / hfq | 适配器接受 hfq | 锚点、历史修订、窗口待 G0-M；与同源入口去重 |
| akshare / eastmoney | EastMoney 经 AKShare；fund_etf_hist_em | ETF | DAILY_BAR | 1d / none | 上游 Bar 原生，字段/指标由 DSA 派生；不要求凭据 | 日线路由/回测 Reader；ETF 样本与源指纹待 G0-M |
| akshare / eastmoney | 同上，保持同一来源关系 | ETF | DAILY_BAR | 1d / qfq | 适配器接受 qfq | 复权基准、修订、窗口待 G0-M |
| akshare / eastmoney | 同上，保持同一来源关系 | ETF | DAILY_BAR | 1d / hfq | 适配器接受 hfq | 复权基准、修订、窗口待 G0-M |
| akshare / sina | 新浪经 AKShare；stock_zh_a_daily | STOCK | DAILY_BAR | 1d / none | 上游 Bar 原生，列名/指标由 DSA 派生；不要求凭据 | 日线路由；仅源码声明，覆盖、窗口待 G0-M；manifest 排除 ETF |
| akshare / sina | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / qfq | 适配器接受 qfq | 锚点、修订、窗口待 G0-M；按实际上游去重 |
| akshare / sina | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / hfq | 适配器接受 hfq | 锚点、修订、窗口待 G0-M；按实际上游去重 |
| akshare / tencent | 腾讯经 AKShare；stock_zh_a_hist_tx | STOCK | DAILY_BAR | 1d / none | 上游 Bar 原生，列名/指标由 DSA 派生；不要求凭据 | 日线路由；与 TencentFetcher 可能同一上游，不重复计独立源 |
| akshare / tencent | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / qfq | 适配器接受 qfq | 价格锚点、窗口待 G0-M；与 TencentFetcher 按实际请求去重 |
| akshare / tencent | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / hfq | 适配器接受 hfq | 价格锚点、窗口待 G0-M；与 TencentFetcher 按实际请求去重 |
| efinance / efinance | 当前本地/容器 0.5.9 的单标 `SHSZQuoteSnapshot` 为 EastMoney HTTPS；股票失败回退全市场 `clist/get` 为 EastMoney HTTP | STOCK | REALTIME_QUOTE | 采样频率、来源日期/时点未知 | 库返回 Provider 数据，DSA 清洗/映射；manifest 不要求凭据 | ThesisLedger/native quote；单标与回退均非独立 EastMoney 备用，回退目标行唯一性已核。旧 alias 与新 `eastmoney`、V1 无 source 共用健康/熔断作用域，Route/Execution 仍保留原 source；完整身份迁移、传输、单位、延迟和授权待 G0-M |
| efinance / efinance | 当前本地/容器 0.5.9 的单标 `SHSZQuoteSnapshot` 为 EastMoney HTTPS；ETF 不走全市场回退 | ETF | REALTIME_QUOTE | 采样频率、来源日期/时点未知 | 同上 | ETF 单标覆盖与字段、单位/时间及真实准入待 G0-M；旧 alias 保留路由身份，与 `efinance/eastmoney` 共用持久请求冷却和健康/熔断作用域，不计独立来源；完整迁移待合同 |
| efinance / efinance | 旧路由 alias；当前 Runtime 与新 source 同调 `get_daily_data`，精确外部 endpoint 待核 | STOCK | DAILY_BAR | 1d / 无显式 adjustment | Bar 来源端数据，DSA 清洗；显式 adjustment 当前拒绝 | 旧/新/V1 无 source 在股票日线内共用健康与熔断；保留原 RouteTarget/Execution，真实价格口径与 G0-M 待验 |
| efinance / efinance | 同一 `get_daily_data`，与新 source 不是独立执行入口 | ETF | DAILY_BAR | 1d / 无显式 adjustment | 同上 | ETF 日线与股票日线健康隔离；旧/新/V1 无 source 共用本组健康与熔断，覆盖/口径及 G0-M 待验 |
| efinance / efinance | 当前 adapter 经独立 Reader 直连 EastMoney HTTPS `FundMNHisNetList`，不再调用 efinance fund SDK | MUTUAL_FUND | FUND_NAV | NAV 日期不是披露时刻；正式性未知 | Reader 完整分页后返回日期序列，Runtime 从中选择最新；不要求凭据 | NAV Reader；旧/新/V1 在本能力内共用健康与熔断，与 AKShare 均属 EastMoney；修订/发布时间/完整性与 G0-M 仍开放 |
| efinance / efinance | 同一 EastMoney HTTPS `FundMNHisNetList` Reader，非第二条独立来源 | MUTUAL_FUND | FUND_NAV_HISTORY | 净值日期序列；更新时间未知 | Reader 总量/分页校验后 DSA 升序归一；不要求凭据 | EfinanceFetcher.get_fund_nav_history → NAV History Reader；旧/新/V1 在本能力内共用健康与熔断，目标 000001 七页样本已通过，历史修订/正式性/完整覆盖与 G0-M 仍开放 |
| efinance / eastmoney | 与旧 alias 同一单标 `SHSZQuoteSnapshot`；股票失败仍可走同源全市场回退 | STOCK | REALTIME_QUOTE | 采样频率、来源日期/时点未知 | 新建默认 RouteTarget 的精确 source，执行身份保留 `eastmoney` | 旧/新/V1 无 source 最近 open 共用健康/熔断，ETF 持久冷却不扩至股票；不计独立备用，字段、单位、时点、传输与 G0-M 待验 |
| efinance / eastmoney | 与旧 alias 同一 ETF 单标快照；禁止全市场回退 | ETF | REALTIME_QUOTE | 采样频率、来源日期/时点未知 | 新建默认 RouteTarget；预算同事务查旧/新/V1 键，健康只读查未过期旧 open，新记录写规范健康键 | 只关闭 600 秒冷却及 60 秒健康/熔断绕行；旧路由/冻结身份和健康行不改，真实覆盖和 G0-M 待验 |
| efinance / eastmoney | 与旧 alias 同一 EastMoney HTTPS `FundMNHisNetList` Reader | MUTUAL_FUND | FUND_NAV | 净值日不等于披露时刻 | 新建默认 RouteTarget，原文和正式性未获新事实 | 旧/新/V1 最近 open 在本能力内共用健康与熔断；来源修订、披露时间、完整性及 G0-M 待验，不算独立备用 |
| efinance / eastmoney | 同一 EastMoney HTTPS 历史净值分页 Reader | MUTUAL_FUND | FUND_NAV_HISTORY | 日期序列，来源可见时间未知 | 新建默认 RouteTarget，不改历史 alias | 旧/新/V1 最近 open 在本能力内共用健康与熔断；完整覆盖、修订与 G0-M 待验，不算独立备用 |
| efinance / eastmoney | EastMoney 来源分类；与旧 alias 同调 Runtime `get_daily_data`，精确 URL 待核 | STOCK | DAILY_BAR | 1d / 无显式 adjustment | Bar 来源端数据，DSA 清洗；显式 adjustment 当前拒绝 | 旧/新/V1 在股票日线内共用健康与熔断；与 AKShare EastMoney 的独立性按真实请求核对，G0-M 待验 |
| efinance / eastmoney | 与旧 alias 同一 Runtime `get_daily_data` | ETF | DAILY_BAR | 1d / 无显式 adjustment | 同上 | 本组旧/新/V1 健康与熔断共用，ETF 与股票隔离；单标覆盖、价格口径与 G0-M 待验，不得当独立备用 |
| tencent / tencent | 腾讯直连；精确 V3 走 `newfqkline/get`，普通旧路径仍走 `fqkline/get`；同一腾讯上游 | STOCK | DAILY_BAR | 1d / none | 精确路径逐年读取 OHLCV 与响应第 8 位原生成交额；按锁定版 AKShare 的手/万元约定换算；股票未实探 | 缺字段、坏窗口或分段证据整窗拒绝；单位、许可与历史覆盖待 G0-M，旧路径不能充当精确证据 |
| tencent / tencent | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / qfq | 同上；股票未实探 | 价格锚点与窗口待 G0-M；STOCK hfq 不登记 |
| tencent / tencent | 同上，保持同一来源关系 | ETF | DAILY_BAR | 1d / none | `159516.SZ` 拆分事件短窗精确接口返回原生量额字段；`fqkline/get` 六字段旧响应仍缺额 | 精确路径保留逐年响应摘要；ETF 单位、历史覆盖、使用条款与准入待 G0-M |
| tencent / tencent | 同上，保持同一来源关系 | ETF | DAILY_BAR | 1d / qfq | 同窗 qfq 价格与 raw 可不同，量额原文一致；不将金额由 qfq 价乘量推算 | qfq 锚点、覆盖和真实准入待 G0-M；hfq 另走独立适配器及准入 |
| tencent / tencent | 腾讯 `newfqkline/get`，请求 `param=...,hfq` 并只读取原生 `hfqday` | ETF | DAILY_BAR | 1d / hfq | 精确 V3 保留 OHLCV 和响应 `raw[8]` 成交额；159516.SZ、2026-04-30..08-09 完整 68 日、短窗重叠 10 日一致；正式 Data V3 已通过 | 上述范围纯价格研究已准入，至北京时间 2026-10-10 01:17:22；目录 ready。量额单位和高级语义未授予；Server 准备尚缺日级证据，回测/重放开放。不得用于 STOCK 或复用 none/qfq 准入 |
| tushare / tushare | Tushare Pro API；实时 quote endpoint 待核 | STOCK | REALTIME_QUOTE | 采样时点、单位未知 | 来源行情原生；requiresCredential=true，DSA 环境 Token | ThesisLedger/native quote；Token、积分和接口权限未知 |
| tushare / tushare | Tushare Pro daily；旧 Contract 未提供 adjustment 维度 | STOCK | DAILY_BAR | 1d / 股票精确 V3 尚未登记 | 来源响应原生；A 股成交量/成交额按 DSA 代码换算；requiresCredential=true | ThesisLedger/native Bar、选股；复权因子权限、单位、历史覆盖待 G0-M |
| tushare / tushare | Tushare Pro fund_daily；Token 与实际 HTTP 地址绑定内部准入修订 | CN ETF | DAILY_BAR | 1d / none；日期分段每段最多 366 日、最多 32 段 | 原始日线，手转份、千元转元；独立日历覆盖校验 | ThesisLedger V3 Bar Reader；M24-b2 本地接线完成，M24-b3 真实权限/覆盖与 G0-M 待验收，默认未准入 |
| tickflow / tickflow | TickFlow 官方 SDK quotes | CN STOCK | REALTIME_QUOTE | 采样时点、频率未知 | 来源行情经 DSA 字段标准化；requiresCredential=true，DSA 环境 API Key | ThesisLedger/native quote；Key、套餐、字段、授权未知 |
| tickflow / tickflow | TickFlow 官方 SDK K-line，period=1d | CN STOCK | DAILY_BAR | 1d / Contract adjustment 未证明 | 成交量有手转股本地标准化；requiresCredential=true | ThesisLedger/native Bar；单位、套餐、范围、口径待 G0-M |
| pytdx / pytdx | 社区 pytdx / 行情服务器协议；不是官方 TdxAiData | CN STOCK | REALTIME_QUOTE | 采样时点、服务器状态未知 | 响应原生，DSA 做格式/字段标准化；内置 | native A 股回退、ThesisLedger quote；服务身份、单位和使用授权未知 |
| pytdx / pytdx | 同上，保持同一来源关系 | CN STOCK | DAILY_BAR | 1d / adjustment 未知 | 行情服务器 K 线原生，DSA 字段标准化；内置 | Runtime 未证明 adjustment；窗口、标识、单位待 G0-M |
| baostock / baostock | BaoStock 服务 | CN STOCK | DAILY_BAR | 1d / native 默认 adjustflag=2（前复权） | 服务响应由 DSA 映射到标准列；内置 | ThesisLedger/native Reader；Contract 无 adjustment 维度，上市历史/授权/目标窗口待核 |
| yfinance / yfinance | Yahoo Finance 经 yfinance；市场代码 CN/HK/US/JP/KR/TW | STOCK | REALTIME_QUOTE | 时点、延迟和币种未知 | Yahoo 数据由库返回，DSA 标准化；内置配置 | native 基本面与 ThesisLedger quote；市场、来源条款和访问可用性待核 |
| yfinance / yfinance | 同上，保持同一来源关系 | ETF | REALTIME_QUOTE | 时点、延迟和币种未知 | 同上 | ETF 市场覆盖与字段待 G0-M |
| yfinance / yfinance | 同上，保持同一来源关系 | INDEX | REALTIME_QUOTE | 时点、延迟和币种未知 | 同上 | 指数代码/市场覆盖待 G0-M |
| yfinance / yfinance | 同上，保持同一来源关系 | STOCK | DAILY_BAR | 1d / auto_adjust=true | 调整序列由 Yahoo/yfinance 返回；DSA 做列名/币种质量整理；内置 | 不能直接等同规范 qfq/hfq；各市场算法和 PIT 未知 |
| yfinance / yfinance | 同上，保持同一来源关系 | ETF | DAILY_BAR | 1d / auto_adjust=true | 同上 | ETF 调整范围、币种、市场和历史覆盖待 G0-M |
| yfinance / yfinance | 同上，保持同一来源关系 | INDEX | DAILY_BAR | 1d / auto_adjust=true | 同上 | 指数覆盖和调整含义待 G0-M |
| longbridge / longbridge | Longbridge OpenAPI | HK/US STOCK | REALTIME_QUOTE | 采样时点、频率未知 | 来源数据经 DSA 标准化；requiresCredential=true；OAuth/Legacy 凭据按实现 | ThesisLedger/native 港美股 Consumer；实际账号权限、单标请求待 G0-M |
| longbridge / longbridge | 同上，保持同一来源关系 | HK/US ETF | REALTIME_QUOTE | 采样时点、频率未知 | 同上 | ETF 单标覆盖/账号权限待 G0-M |
| longbridge / longbridge | 同上，保持同一来源关系 | HK/US STOCK | DAILY_BAR | 1d / NoAdjust API option | API 调整选项存在；Contract 未证明可选或精确基准；DSA 标准化 | raw 语义、窗口与账号覆盖待 G0-M |
| longbridge / longbridge | 同上，保持同一来源关系 | HK/US STOCK | DAILY_BAR | 1d / ForwardAdjust API option | API 调整选项存在；不得未经探针映射为规范 qfq | 基准、窗口和 Contract 可选性待 G0-M |
| longbridge / longbridge | 同上，保持同一来源关系 | HK/US ETF | DAILY_BAR | 1d / NoAdjust API option | API 调整选项存在；Contract 未证明可选或精确基准；DSA 标准化 | ETF raw 口径和范围待 G0-M |
| longbridge / longbridge | 同上，保持同一来源关系 | HK/US ETF | DAILY_BAR | 1d / ForwardAdjust API option | API 调整选项存在；不得未经探针映射为规范 qfq | ETF 基准、窗口和 Contract 可选性待 G0-M |
| finnhub / finnhub | Finnhub API | US STOCK | REALTIME_QUOTE | quote 时点、延迟和单位未知 | 来源数据，DSA 做字段/行情结构标准化；requiresCredential=true，API Key | native 美股 fallback、ThesisLedger quote；Key/套餐权限待核 |
| finnhub / finnhub | 同上，保持同一来源关系 | US STOCK | DAILY_BAR | 1d / adjustment 未知 | 来源 Bar，DSA 标准化；requiresCredential=true | 修订历史、时区/单位、目标窗口待 G0-M；不宣称免费或可用 |
| alphavantage / alphavantage | Alpha Vantage API | US STOCK | REALTIME_QUOTE | quote 时点、交易所时区、单位未知 | 来源数据，DSA 做字段/行情结构标准化；requiresCredential=true，API Key | native 美股 fallback、ThesisLedger quote；Key/配额/权限待核 |
| alphavantage / alphavantage | 同上，保持同一来源关系 | US STOCK | DAILY_BAR | 1d / adjustment 未知 | 来源 Bar，DSA 标准化；requiresCredential=true | 复权、时间戳/时区、修订和窗口待 G0-M |

当前源码实际登记 15 个 Provider × manifest source ID，均在上表展开；P02-a 文档中的“14 个”与当前 manifest 静态计数相差 1，本目录按源码列出的 upstream_sources 计数。Efinance manifest version 2 已在 `eastmoney` 精确 source 下登记报价和净值，AKShare manifest version 3 在同一精确 source 下登记净值与股票报价；新建默认 Desired Policy 使用这些 source，表中的 `efinance/efinance`、AKShare 净值 `akshare/akshare` 行保留为旧 alias 库存，不代表另一实际上游。旧路由与执行元数据尚未迁移，不能计为独立备用；AKShare EastMoney、Efinance EastMoney、AKShare Tencent 与 TencentFetcher 的独立性仍须按实际请求确认。

## 4. manifest 之外的现有能力与缺口

每行仍按 Provider × 实际上游 × 单一资产 × 单一能力 × 周期/口径登记。AKShare 的多个候选函数与 Tushare 的多个行业接口是运行时回退选项；实际上游未知或未锁定的行仍是来源选择项，不宣称多个独立备用。已由 §3 登记的 NAV、NAV History、Holdings 不重复计数，其 endpoint、消费方与口径细节已并入对应原子行。

| Provider | 实际上游 / endpoint | 单一资产 | 单一能力 | 周期 / 口径 | 现有实现与性质 | Consumer、权限、缺口与 R/G0 |
| --- | --- | --- | --- | --- | --- | --- |
| exchange-calendars | 本地安装包中的 XSHG schedule | CN 交易所日历 | TRADING_CALENDAR | 包版本覆盖窗口、交易时段；实际范围随版本 | calendar_fact() 读取并附版本/范围，属于本地派生，不需外部 Provider 授权 | V2 数据依赖/可交易性 Consumer；缺包或超范围 unavailable；特殊休市公告完整性未知。R01.4、V-DSA |
| AKShare | fund_announcement_dividend_em；实际 HTTP 上游未逐请求核实 | ETF | CORPORATE_ACTION_NOTICE | 公告日、可见日未知 | AkshareFundamentalAdapter.get_corporate_actions_v2() 候选输入；来源公告经 DSA 日期/事件归一化 | 已有公司行动依赖 Reader；账号、条款、完整性未知；标题不足以定拆分 ratio/effective time。R07.1、M22/M23、G0-M |
| AKShare | stock_fhps_detail_em；远端上游身份未知 | CN STOCK | CORPORATE_ACTION_CANDIDATE | 事件日、公告/可见时间未知 | 股票 get_corporate_actions_v2() 的候选回退之一；事件标准化由 DSA 派生 | Consumer 与权限同上；只有运行时 source 字段才能确认实际候选。R07.1、G0-M |
| AKShare | stock_history_dividend_detail；远端上游身份未知 | CN STOCK | CORPORATE_ACTION_CANDIDATE | 事件日、公告/可见时间未知 | 同一股票适配的候选回退之一；不与其他函数合并为已确认单一 endpoint | Consumer/权限同上；接口完整性、事件类型待核。R07.1、G0-M |
| AKShare | stock_dividend_cninfo；CNINFO 实际请求与许可待核 | CN STOCK | CORPORATE_ACTION_CANDIDATE | 事件日、公告/可见时间未知 | 同一股票适配的候选回退之一；事件标准化由 DSA 派生 | Consumer/权限同上；候选上游与其他 AKShare 包装不得预先算成独立备用。R07.1、G0-M |
| AKShare | 首选 stock_financial_abstract／CompanyFinanceService.getFinanceReport2022；analysis 回退合同未准入 | CN STOCK | FINANCIALS | 原表报告期为列，原文有 publish_date；可见时间、修订及日期语义未准入 | get_fundamental_bundle() 现用首行映射与原表方向不符；本机1.18.94安装版丢弃 item_tongbi、币种及 publish_date | get_fundamental_context() → Analyzer/Research Agent/Screening；600004 原样采集完成，证券身份、金额倍率/比例单位仍缺；非历史 PIT，最新值不得进入严格历史回测。R05.1、G0-M |
| AKShare | valuation bundle 使用的具体函数/HTTP endpoint 未锁定 | CN STOCK | VALUATION | 观察日、时点、修订未知 | 与财报字段分开登记；DSA 映射/组装 | 既有研究 Consumer；须逐字段核实 PE/PB/市值单位与滞后；现值不得当历史 PIT。R05.2、G0-M |
| Yahoo Finance / yfinance | Yahoo Finance；具体 fundamentals endpoint 未锁 | 非 CN STOCK | FINANCIALS | 季度 DataFrame 列报告期可校验；`.info` 汇总期间未知 | YfinanceFundamentalAdapter 返回来源数据，DSA 映射/组合 | get_fundamental_context() 的研究 Consumer；[US 财务期间对齐本地证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r05-yahoo-period-alignment.md)，条款/真实访问、原发布与修订未知，不具 PIT 保证。R05.3、R05.4、G0-R |
| Yahoo Finance / yfinance | Yahoo Finance；具体 valuation endpoint 未锁 | 非 CN STOCK | VALUATION | 最近可得观察值；as-of/revision 未知 | 来源数据由库返回，DSA 做字段映射/组合 | 同一研究 Consumer；单位、币种、字段时点待逐项核对。R05.5、G0-R |
| AKShare | 新浪经 AKShare；stock_zh_index_spot_sina | CN INDEX | INDEX_QUOTE | 行情快照；延迟、as-of 未知 | AkshareFetcher.get_main_indices() 返回来源快照，DSA 清洗/映射 | DataFetcherManager/MarketAnalyzer/Market Review；不合并成 EastMoney 行情源。R06.1、G0-M |
| Efinance | 本地及当前 DSA 容器 `efinance 0.5.9` 的 get_realtime_quotes(沪深系列指数) → EastMoney `http://push2.eastmoney.com/api/qt/clist/get`；安装包静态源码哈希一致 | CN INDEX | INDEX_QUOTE | 接口返回快照；频率/as-of 未知 | EfinanceFetcher.get_main_indices() 按六位代码及市场 `行情ID` 唯一行过滤/映射 | 大盘 Consumer；该入口是 EastMoney 包装，不能计为独立备用。依赖仅有 `>=0.5.5`，未来构建版本、实际 HTTP 传输、单位/时点/权限仍待 G0-M。R06.1 |
| Tushare Pro | index_daily | CN INDEX | INDEX_QUOTE | 1d 收盘值；查询最近五个日历日并选最新非空行 | 来源日线被包装成指数最新值；amount 由千元转元是 DSA 派生 | 大盘 Consumer；并非实时行情，交易日/时点及接口权限待核。R06.1、G0-M |
| TickFlow | 官方 SDK quotes | CN INDEX | INDEX_QUOTE | SDK 报价快照；延迟/as-of 未知 | 来源报价由 DSA 选择代码、字段标准化 | 大盘 Consumer；Key/套餐、单位、权限未知。R06.1、G0-M |
| AKShare | EastMoney 经 AKShare；stock_board_industry_name_em | CN 行业板块 | SECTOR_RANKING | 行业涨跌快照；as-of 未知 | AkshareFetcher.get_sector_rankings() 优先候选；DSA 排序/字段映射 | MarketAnalyzer/Market Review/Agent Market Tools；定义版本与来源快照待核。R06.2、G0-M |
| AKShare | 新浪经 AKShare；stock_sector_spot(indicator=行业) | CN 行业板块 | SECTOR_RANKING | 行业涨跌快照；as-of 未知 | 上一行失败后的候选回退；DSA 排序/字段映射 | 保留与 EastMoney 关系及回退顺序；不可把两个包装函数算成同时确认的独立源。R06.2、G0-M |
| Efinance | 当前本地/容器 0.5.9 的 get_realtime_quotes(行业板块) → EastMoney HTTP `clist/get`；静态路径已核 | CN 行业板块 | SECTOR_RANKING | 行业涨跌快照；as-of 未知 | EfinanceFetcher.get_sector_rankings()；DSA 排序/字段映射 | MarketAnalyzer/Market Review/Agent Market Tools；分类版本、真实响应/单位/时间、传输和 G0-M 待核，不与 AKShare EastMoney 候选算独立源。R06.2 |
| Tushare Pro | Tushare Pro API endpoint moneyflow_ind_ths（THS 分类） | CN 行业板块 | SECTOR_RANKING | trade_date；仅 15:30 后尝试当天数据 | TushareFetcher.get_sector_rankings() 第一候选；DSA 做涨跌排序 | 行业分类与东财接口定义不同；权限/时点待核。R06.2、G0-M |
| Tushare Pro | Tushare Pro API endpoint moneyflow_ind_dc（EastMoney 分类） | CN 行业板块 | SECTOR_RANKING | trade_date；同一日历门槛 | 同一方法在 THS 失败后的候选回退；DSA 排序 | 不与 THS 结果合并成同一分类；实际选中来源需保留。R06.2、G0-M |
| TickFlow | SDK universes.list/batch + quotes.get | CN 行业板块（SW1） | SECTOR_RANKING | quote 快照；as-of 未锁 | 本地用 SW1 universe 与报价计算涨幅排名，属于 DSA 派生 | MarketAnalyzer/Market Review；universe_quotes 套餐权限可能不可用，分类与时点待核。R06.2、G0-M |
| AKShare | EastMoney 经 AKShare；stock_board_concept_name_em | CN 概念板块 | CONCEPT_RANKING | 概念涨跌快照；as-of 未知 | AkshareFetcher.get_concept_rankings()；DSA 排序/字段映射 | DataFetcherManager/MarketAnalyzer/Agent Consumer；来源与分类版本待核。R06.2、G0-M |
| AKShare | stock_individual_fund_flow；显式 stock/market，缺完整场所时拒绝，远端 upstream 未核 | CN STOCK | CAPITAL_FLOW | 有效日期唯一时选最新交易日；来源可见时刻未知 | get_capital_flow() 只读取精确 `主力净流入-净额`；坏日表拒绝股票块，行业块独立 | [选行本地证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-flow-latest-day.md)；请求身份和CN缓存隔离本地通过，金额单位、算法、原响应身份及真实准入未通过。R06.12、G0-M |
| AKShare | stock_individual_fund_flow；同一接口候选关系 | CN STOCK | CAPITAL_FLOW | 5 日累计字段在安装版日表中不存在 | 同一 Consumer 的 `inflow_5d` 保持未知 | 不从每日净额推算累计；若需能力，另选接口并核单位/日期/算法。R06.13、G0-M |
| AKShare | stock_individual_fund_flow；同一接口候选关系 | CN STOCK | CAPITAL_FLOW | 10 日累计字段在安装版日表中不存在 | 同一 Consumer 的 `inflow_10d` 保持未知 | 不从每日净额推算累计；若需能力，另选接口并核单位/日期/算法。R06.14、G0-M |
| AKShare | stock_main_fund_flow；EastMoney 全市场排名快照 | CN STOCK 排名集合 | CAPITAL_FLOW_RANKING，非单股净额 | `symbol` 为市场类别；无逐股交易日 | 当前 `get_capital_flow()` 不调用；不能作个股净额回退 | [候选失配证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-main-flow-candidate.md)；今日列仅净占比/排名/涨跌幅，R06.15 净额能力 unavailable |
| AKShare | stock_main_fund_flow；同一排名快照 | CN STOCK 排名集合 | CAPITAL_FLOW_RANKING，非 5 日净额 | 5 日排行榜字段为净占比、排名、涨跌幅 | `inflow_5d` 不从百分比映射 | R06.16 此候选 unavailable；备用来源需另选并核合同 |
| AKShare | stock_main_fund_flow；同一排名快照 | CN STOCK 排名集合 | CAPITAL_FLOW_RANKING，非 10 日净额 | 10 日排行榜字段为净占比、排名、涨跌幅 | `inflow_10d` 不从百分比映射 | R06.17 此候选 unavailable；备用来源需另选并核合同 |
| AKShare | stock_sector_fund_flow_rank；显式“今日/行业资金流” | CN 行业板块 | CAPITAL_FLOW 当前排名 | 今日快照；来源时刻未知 | get_capital_flow() 只读唯一精确主力净流入净额列并生成行业 top/bottom；空金额过滤 | [本地排名证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-sector-flow-rank.md)；金额单位/来源日期及真实准入未证，R06.18、G0-M |
| AKShare | stock_sector_fund_flow_summary；指定单个行业 | CN 行业内股票集合 | 行业内个股资金流，非行业排名 | 默认“电源设备”；当前快照时点未知 | 不再作为 get_capital_flow() 行业排名失败回退；当前研究 Consumer 未另接行业成员表 | R06.19 需独立行业标识、成员与消费合同；不能把个股名当行业，G0-M 未过 |
| AKShare | stock_lhb_stock_statistic_em | CN STOCK | DRAGON_TIGER_EVENT_INDEX 候选 | 本机近 20 日查找；事件日/披露日未知 | get_dragon_tiger_flag() 候选一，仅进入当前基本面聚合 | [Consumer 核对](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-dragon-consumer.md)未发现历史截点/回测入口；R06.20–21 历史接线 blocked，G0-M 未过 |
| AKShare | stock_lhb_detail_em | CN STOCK | DRAGON_TIGER_EVENT_INDEX 候选 | 事件日/披露日未知 | 同一当前聚合的候选二；未建立独立历史适配 | R06.22 依赖历史 Consumer 与可见时刻，当前 blocked；不得算第二独立已准入来源 |
| AKShare | stock_lhb_jgmmtj_em | CN STOCK | DRAGON_TIGER_EVENT_INDEX 候选 | 事件日/披露日未知 | 同一当前聚合的候选三；未建立独立历史适配 | R06.23 依赖历史 Consumer 与可见时刻，当前 blocked |
| 用户配置 RSS/Atom | 每个配置 URL 是独立实际来源；当前具体 URL 集未知 | NEWS_ITEM | NEWS_INDEX | feed 发布时间 + DSA 抓取时间 | IntelligenceService 解析、去重、存储；标题/摘要/时间来自 feed | 个股/Agent/大盘 Consumer best-effort；这是配置来源选择汇总行，具体 URL 应逐个登记后才可执行；自动抓取要求配置合法源并显式开启 NEWS_INTEL_AUTO_FETCH_ENABLED；许可、时间准确度因 feed 而异。R07.2 |
| NewsNow connector | NewsNow 实例/聚合上游；CLS source id 的原始发布方未知 | NEWS_ITEM | NEWS_INDEX | payload 日期 + 抓取时间 | GET {NEWSNOW_BASE_URL}/api/s?id=CLS；DSA 解析/存储 | 默认公开实例非官方；实例授权/可用性和原始资讯授权未知；不作行情/事件事实。R07.2、G0-R |
| NewsNow connector | 同一实例关系；Xueqiu source id 的原始发布方未知 | NEWS_ITEM | NEWS_INDEX | payload 日期 + 抓取时间 | 同一路由，source id=Xueqiu；原始 upstream 未逐项识别 | 同上；用户显式开启自动抓取后才请求。R07.2、G0-R |
| NewsNow connector | 同一实例关系；WallstreetCN source id 的原始发布方未知 | NEWS_ITEM | NEWS_INDEX | payload 日期 + 抓取时间 | 同一路由，source id=WallstreetCN；原始 upstream 未逐项识别 | 同上；自然语言/热榜不作确定性事实。R07.2、G0-R |
| NewsNow connector | 同一实例关系；Jin10 source id 的原始发布方未知 | NEWS_ITEM | NEWS_INDEX | payload 日期 + 抓取时间 | 同一路由，source id=Jin10；原始 upstream 未逐项识别 | 同上；实例/来源身份与授权待 G0-R。R07.2、G0-R |
| NewsNow connector | 同一实例关系；Gelonghui source id 的原始发布方未知 | NEWS_ITEM | NEWS_INDEX | payload 日期 + 抓取时间 | 同一路由，source id=Gelonghui；原始 upstream 未逐项识别 | 同上；实例/来源身份与授权待 G0-R。R07.2、G0-R |
| free-stockdb | 镜像地址、数据源链未知 | 资产未知 | 能力未知 | 周期/口径/完整度未知 | 未发现安装、读取适配或导入 Consumer | 来源选择行，未有镜像、许可、可达性证据；G0-R 通过前不创建 Provider/启用导入。R08 |

### 现有消费者入口速查

- ThesisLedger 日线/报价/NAV/持仓/筹码：src/services/thesis_ledger_control.py、src/services/thesis_ledger_provider_runtime.py、api/thesis_ledger.py；当前 manifest 只声明有限的 Contract V1 能力。
- ThesisLedger 目录：src/services/thesis_ledger_catalog.py；现有 Provider Loader 仅 AKShare 和 Efinance，目录构建不能证明所有股票/ETF/基金状态历史完整。
- 回测交易日历：src/services/thesis_ledger_v2_dependencies.py:calendar_fact() 与 src/services/thesis_ledger_v2_tradability.py；有交易所日历派生，不提供历史停牌事实。
- DSA 原生个股日线/报价与 fallback：data_provider/base.py:DataFetcherManager；大盘/板块研究经 MarketAnalyzer、market_hotspot_service 和 src/agent/tools/market_tools.py。
- DSA 基本面/资金流：data_provider/fundamental_adapter.py、data_provider/yfinance_fundamental_adapter.py、DataFetcherManager.get_fundamental_context/get_capital_flow_context；报告、Research Agent、Screening 有既有入口。
- DSA 资讯索引：src/services/intelligence_service.py、docs/intelligence-sources.md；用户启用的抓取源和系统消费者不能视作实时 Provider 路由。

## 5. R01–R08 原子叶子建议

本节把原 29 条建议展开为 **117 个编号条目**：116 个固定编号叶子，以及按每个显式 RSS/Atom 配置 URL 展开的 R07.8/{sourceId} 模板叶子。编号仅在本目录内稳定。每个叶子只涵盖一个来源（或一个待选择来源）、一个资产类型、一个能力/周期口径和一个最近端 Consumer；多个 SDK 包装同一上游时保留同源关系。这里保留 P02-c 的任务分解及历史候选基线，后续实现状态按对应叶子的证据更新；待选择、本地实现、目标拒绝验证和真实正向准入须分别判断，目录登记不代表真实来源可用。

2026-09-28 当前状态摘要（仅核对以下已有证据，不是全部叶子的完成审计）：R01.5 安全解析器及有界分页读取器已完成本地验证，基金分类、Catalog 消费接线及真实完整成功仍开放，见 [目录来源证据](thesis-ledger-catalog-source-evidence.md)。R01.10 已完成日历专项及目标 HTTP→Client→完整冻结→离线重放，范围及容器可写层限制见 [主仓日历发布 Task](../../thesis-ledger/docs/tasks/2026-09-27-calendar-release-availability.md)。R07.25/R07.26 的 RQData 身份/币种原文合同、生产事件接线与目标拒绝验证已完成；真实身份/币种审核、逐接口权限、完整历史及目标正向请求仍开放，见 [主仓 RQData 生产接线证据](../../thesis-ledger/docs/tasks/evidence/2026-09-27-rqdata-event-runtime.md)。其他未选 endpoint、Consumer 和逐单元准入仍依各自任务推进；AC20 与 F02 父项不据此关闭。

**Owner 与输出约定：**“选择”叶由 DSA 来源目录责任人只读核实现有源码/配置并只写本目录；输出一个确定的 Provider、实际上游/endpoint、资产、能力/口径、现有 Consumer、许可与阻塞结论，无法确认时明确 blocked 并回填 Spec 问题。“适配”叶由表中 DSA 路径所有者负责单来源适配、定向测试及本目录事实更新；跨仓 Consumer 只读，不把 Server、Desktop、部署工作塞入 DSA 叶。选择叶完成不等于适配完成。

**通用依赖与停止条件：**每个真实集成叶依赖 P02、相应 C04 数据契约接缝和对应 G0；价格/行情叶另依赖 S03–S05 相关价格事实与路由契约，公司行动叶遵循 M22/M23。HiThink 用 G0-H，已登记市场来源用逐源 G0-M，辅助/许可/镜像来源用 G0-R。选择叶可以只读盘点；没有现有 Consumer、接口冲突、权限不足或来源身份不能锁定时，只交付阻塞证据，不虚构 endpoint、不增加新 Consumer、不启用来源。真实 G0、Provider、产品、数据库、UI 或部署门禁均未在本轮执行。

### R01 基础身份与日历（12）

Data V3 时间说明：原生历史行情的 `timestamp` 保留原始日期，`completionStatus` 根据当日收盘判定；当来源未给出可核实的历史版本可用时间时，`availableAt` 记录实际抓取观测时点，与 `sourcePriceBasis.observedAt` 一致。Server 必须显式使用固定快照研究时钟；此信息不能证明严格 PIT 重建可用，不能回填成历史收盘时间。

2026-09-25 续接：`thesis_ledger_market_v3_facts.py` 的深市日历依据[深交所 2026 年休市公告](https://www.szse.cn/disclosure/notice/t20251222_618087.html)，覆盖 2026-01-01..12-31，修订为 `szse-2026-holidays-t20251222_618087-year-v1`。交易日按公告的节假日及周末确定，调休周末不开放；该范围可供 159516 策略预热。此项只扩日历证据，上市登记仍限已有独立事实，不自动扩大行情 admission，不证明所有日期的证券可交易性；2025/2027、HK/US 等未核实范围仍不可用。历史快照保留原有日历证明及指纹。本文为本 Fork 专属单语能力记录，无对应英文文档需要同步。

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R01.1 | AKShare × STOCK × INSTRUMENT_CATALOG → ThesisLedger Catalog Builder；src/services/thesis_ledger_catalog.py | code/name、显式 STOCK、920→BJ、坏行与同源冲突拒绝；29 项回归通过并已同步目标。目标 1.18.97 首次失败后重试成功，股票 5,569 条，三个官方上游函数和内容摘要已记录 | 待完整 Catalog Consumer 刷新与准入对账；股票函数成功不证明同时读取 ETF/基金的整个 loader 成功。见 catalog-source-evidence；当前目录不证明历史身份 |
| R01.2 | AKShare × ETF × INSTRUMENT_CATALOG → 同一 Catalog Builder；同上 | §3 AKShare loader/ETF 候选 → ETF 目录字段与单测；若 loader 没有该资产能力则记录 blocked | P02、G0-M；固定 ETF 覆盖、代码/名称字段、刷新范围；不得从股票覆盖外推 |
| R01.3 | AKShare × MUTUAL_FUND × INSTRUMENT_CATALOG → 同一 Catalog Builder；同上 | §3 AKShare loader/基金候选 → 基金目录字段与单测；若 Reader 不接受基金则回填 Consumer 缺口 | P02、G0-M；固定基金代码和分类字段；不得把基金当 ETF |
| R01.4 | Efinance × STOCK × INSTRUMENT_CATALOG → ThesisLedger Catalog Builder；src/services/thesis_ledger_catalog.py | 目标 0.5.9 默认股票过滤与 EastMoney clist 入口已核实；适配显式 STOCK，目录全入口移除前缀猜类型，相关 32 项测试通过 | 真实读取/分页覆盖及准入仍开放；与 AKShare 的 EastMoney 入口同源，不作为独立备用；详见来源证据 |
| R01.5 | Efinance Catalog fallback / EastMoney `rankhandler.aspx` × MUTUAL_FUND × INSTRUMENT_CATALOG → 同一 Catalog Builder；同上 | 当前 EastMoney 页面与安装版 AKShare 均把该入口定义为开放基金排行；请求显式固定 `dt=kf,ft=all`，只投影 `MUTUAL_FUND/OF`，不从代码/名称/计数猜 ETF。安全 parser、分页 Reader、HTTP 边界和 Catalog 消费/Job 回归当前 68 项通过；目标 Efinance 0.5.9 缺 `fund.get_realtime_quotes` 时使用该 fallback，若原生方法存在则仍优先原生方法 | 本地消费接线与可终止 Provider 进程硬期限已完成；坏分页/重复/变化/超时使整个 Efinance 来源原子失败，不发布部分股票+基金目录。完整真实读取首次及两次重试仍均 ReadTimeout，预算已耗尽，故真实完整覆盖/连续可用性与历史目录资格未通过，不能宣称基金目录在线。见 [目录来源证据](thesis-ledger-catalog-source-evidence.md) |
| R01.6 | 选择：BaoStock × CN STOCK × CODE_NAME → DataFetcherManager.get_stock_name；Owner：DSA 来源目录责任人 | 已选择既有名称 Reader，stock_service 与研究流程消费；固定 query_stock_basic 的 code/code_name，返回当前名称或 None，不提供历史名称 | 选择已完成；真实来源及范围准入继续归 R01.7/G0，当前名称缓存不证明历史有效期，无 Bar 不推断停牌 |
| R01.7 | BaoStock × CN STOCK × CODE_NAME → DataFetcherManager.get_stock_name；Owner：data_provider/baostock_fetcher.py 与对应测试 | 唯一响应、代码一致、非空名称、最终状态及 Manager 别名缓存回归共 25 项通过；目标源码一致，BaoStock 0.9.4 Fetcher 与受控 Manager 真实探针均首次成功，别名请求只调用一次来源 | 单标的 Manager 链路已验证；默认多源优先级及完整支持范围门禁仍开放，不证明历史名称或挂牌状态；详见来源验收证据 |
| R01.8 | 选择：BaoStock × CN STOCK × SECURITY_LIST → 一个目录 Reader；Owner：DSA 来源目录责任人 | 已找到 DataFetcherManager.prefetch_stock_names(use_bulk=True)，但它只消费 code/name；当前 get_stock_list 丢弃 ipoDate/outDate/type/status，无上市状态消费者 | 范围缺口仍开放：不能把名称预取当作历史证券列表 Reader，R01.9 暂不宣称已接入；需明确上市状态消费契约 |
| R01.9 | 继承 R01.8 唯一选择：BaoStock × CN STOCK × SECURITY_LIST → 指定 Reader；Owner：data_provider/baostock_fetcher.py 与对应测试 | R01.8 选择记录 → 单字段适配、Reader 接入和定向测试 | 依赖 R01.8、P02、G0-M；as-of 未确认则保持 unavailable |
| R01.10 | exchange-calendars × XSHG × TRADING_CALENDAR → calendar_fact()/回测依赖 Consumer；src/services/thesis_ledger_v2_dependencies.py | 已完成：固定 4.13.2，93 文件源码树绑定公开制品时间，特殊休市/边界/发布前拒绝回归通过；官方完整更新及后续代码同步完成，目标实际 HTTP→Client→完整快照→离线重放通过 | 发布前当前版本不可用，不声称各公告最早可见性；仅日历能力验收，合成行情不计真实回测。新语义在容器可写层，重建需官方部署；证据见 [主仓日历发布 Task](../../thesis-ledger/docs/tasks/2026-09-27-calendar-release-availability.md) |
| R01.11 | 来源待选 × CN STOCK × HISTORICAL_LISTING_STATUS → 一个现存历史状态 Reader；Owner：DSA 来源目录责任人 | §2/§4 无已确认历史状态来源/Consumer → 只选择一个合规 Provider、endpoint、as-of 字段和 Reader；无 Consumer 则回填 Spec 并 blocked | P02、G0-M；能区分挂牌/停牌状态与缺失 Bar，并证明日期有效性 |
| R01.12 | 继承 R01.11 唯一选择 × CN STOCK × HISTORICAL_LISTING_STATUS → R01.11 指定 Reader；Owner：选择结论确定的单一 DSA adapter 路径 | R01.11 的选择记录 → 单来源适配、Reader 单测和一组有据样本 | 依赖 R01.11、P02、G0-M；未选来源或未确认 Consumer 前不得派发实现 |

### R02 报价与补充行情（32）

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R02.1 | 已选择 HiThink `a-share-prices-snapshot` × STOCK × REALTIME_QUOTE → 现有 `ProviderRuntime` Quote Reader；Owner：DSA 来源目录责任人 | 官方[股票快照合同](https://fuyao.aicubes.cn/docs/api-reference/prices/)确认 `thscodes` 显式单标不分页、身份/量价字段及可空上游时间；目标容器 `600519.SH` 单次 HTTP 200、原响应摘要已记录 | 选择及该账号单标只读探针完成；本地接线见[实施证据](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-hithink-quote-runtime-local.md)，目标准入与长期可用性未验 |
| R02.2 | 继承 R02.1 的 STOCK 精确来源 → 同一 Quote Reader；Owner：`src/services/thesis_ledger_hithink_quote.py` 与 ProviderRuntime | 当前 V2 精确准入、完整 `thscode`、环境凭据快照、来源/时钟/单位和读后复核已在隔离 SQLite、鉴权 HTTP 验证 | 本地执行接线通过；目标策略仍无 HiThink Quote、未签发真实准入，真实 G0-H 与目标消费者继续开放 |
| R02.3 | 已选择 HiThink `fund-market-snapshot` × ETF × REALTIME_QUOTE → 现有 `ProviderRuntime` Quote Reader；Owner：DSA 来源目录责任人 | 官方[基金快照合同](https://fuyao.aicubes.cn/docs/api-reference/fund-market/)确认单只带后缀 `thscode`、ETF/LOF 与股票端点不同；目标容器 `510300.SH` 单次 HTTP 200、原响应摘要已记录 | 选择及该账号单标只读探针完成；ETF 量额单位仍为 `unknown`，目标准入与长期可用性未验 |
| R02.4 | 继承 R02.3 的 ETF 精确来源 → 同一 Quote Reader；Owner：`src/services/thesis_ledger_hithink_quote.py` 与 ProviderRuntime | ETF 独立准入、完整代码、600 秒单标预算、未知量额单位公开合同与晚到撤销已在[本地实施证据](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-hithink-quote-runtime-local.md)验证 | 本地执行接线通过；真实 ETF 范围/量额单位、目标时点及 Server→目标 DSA 消费继续开放 |
| R02.5 | AKShare/实际选中上游 × STOCK × REALTIME_QUOTE → ProviderRuntime/native Quote；src/services/thesis_ledger_provider_runtime.py | §3股票报价中的stock_zh_a_spot_em候选已补唯一精确目标原行，[本地证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m3-eastmoney-quote-identity.md)含独立股票正反例；其他候选/同源指纹仍按原范围 | 仅此候选行选择通过；P02/C04/G0-M、频率/时点/单位/延迟/权限仍未通过，不宣称全部报价来源完成 |
| R02.6 | AKShare/实际选中上游 × ETF × REALTIME_QUOTE → ProviderRuntime/native Quote；同上 | §3 ETF报价中的fund_etf_spot_em候选已补独立唯一行测试，与R02.5同[实施证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m3-eastmoney-quote-identity.md)但分别验证入口 | 仅ETF该候选行选择通过，其他候选、字段/单位/时点及P02/C04/G0-M仍开放；不继承股票样本结论 |
| R02.7 | Efinance/EastMoney × STOCK × REALTIME_QUOTE → native Quote Consumer；ProviderRuntime | 单标 SHSZQuoteSnapshot 入口已核实，唯一行及精确代码修复；股票失败后的全市场回退另补目标唯一行拒绝，[本地证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r02-7-efinance-fallback-identity.md)。目标 600519 首次真实单标探针成功 | 单标 HTTPS 与回退 HTTP 都是 EastMoney；目标 API 已验证未知时间保持 unknown；回退真实传输、完整单位/范围准入仍开放，不能与其他 EastMoney 包装计为独立备用 |
| R02.8 | Efinance/EastMoney × ETF × REALTIME_QUOTE → native Quote Consumer；ProviderRuntime | 独立 159516 单标快照首次真实成功，沿用禁止全市场回退的现有专用入口；代码与名称匹配且量额字段存在 | 时间、量额单位与完整范围仍缺证据，不从股票样本或字段存在外推准入；详见报价来源证据 |
| R02.9 | TencentFetcher × CN STOCK × REALTIME_QUOTE → 无现存报价入口；Owner：DSA 来源目录责任人 | 选择核对已完成：宿主源码及目标实际类均无 get_realtime_quote，只有 fqkline 日线入口，故此组合 unsupported；既有腾讯报价位于 AkshareFetcher，不重复登记 | 此组合不准入。目标只读反射确认方法不可调用；实际 AKShare 腾讯报价继续归 R02.5 的来源选择与验证，不由日线能力外推 |
| R02.10 | 继承 R02.9：TencentFetcher × CN STOCK × REALTIME_QUOTE → 无 Reader；Owner：DSA 来源目录责任人 | R02.9 确认 unsupported，本轮跳过直接 TencentFetcher 报价实现；已有 AKShare 腾讯报价不得复制成第二入口 | 不计实现通过；未来改变组合需先明确独立消费契约，当前真实报价门禁仍由 R02.5 承担 |
| R02.11 | AKShare/Sina × CN INDEX × INDEX_QUOTE → DataFetcherManager/MarketAnalyzer | §4 stock_zh_index_spot_sina 行 → 完整代码唯一精确身份选择及既有消费者离线接缝已完成，见[组合回归](../../thesis-ledger/docs/tasks/evidence/2026-09-28-m3-index-local-regression.md)与[源码 Review](../../thesis-ledger/docs/tasks/evidence/2026-09-28-m3-index-review.md) | P02、G0-M；来源/as-of、交易阶段、单位及真实准入仍待验证；本地选择不授予时点资格 |
| R02.12 | Efinance/当前容器 0.5.9 静态确认 EastMoney × CN INDEX × INDEX_QUOTE → 大盘 Consumer | §4 `get_main_indices()` 已补六位代码、市场 `行情ID` 与唯一原行选择；[来源及身份分层证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r02-12-efinance-upstream-identity.md) | 本地 getter 与当前容器包源码已核；当前应用代码未同步、实际 HTTP 传输、G0-M、真实时点/单位/覆盖及 Consumer 准入仍开放，不能标成实时 Bar 或独立 EastMoney 备用 |
| R02.13 | Tushare Pro/index_daily × CN INDEX × INDEX_QUOTE → 大盘 Consumer | §4 index_daily 行 → 精确身份、合法窗口日期、唯一日期与最大日期原行选择、五日窗口及千元转元离线验证已完成，见[组合回归](../../thesis-ledger/docs/tasks/evidence/2026-09-28-m3-index-local-regression.md)与[源码 Review](../../thesis-ledger/docs/tasks/evidence/2026-09-28-m3-index-review.md) | P02、G0-M；日线收盘值包装，真实权限、来源时点与准入仍待验证，不宣称实时行情 |
| R02.14 | TickFlow 0.1.25 `quotes.get` × CN INDEX × INDEX_QUOTE → DataFetcherManager/MarketAnalyzer | 固定 6 个主指数并按批请求；只消费本批 target symbol，同一 target 重复整体拒绝；每行必须有可解析 provider timestamp，返回 `source=tickflow:quotes.get:index` 与 `as_of`。点位/变动单位=`index_point`、涨跌幅=`percent`；volume/amount 数值保留但单位显式 `unknown`，不从股票合同外推。专属3红例后修复，TickFlow/大盘 Consumer 合并60项通过 | 本地适配完成；P02/G0-M、真实 Key/套餐权限、实际覆盖、timestamp 源端语义、量额单位、交易阶段/延迟和目标运行仍开放，不宣称在线或历史 PIT |
| R02.15 | 来源待选 × CN STOCK × INTRADAY_BAR/1m → ThesisLedger Risk/Automation 分钟线 Consumer；Owner：DSA 来源目录责任人 | 当前 V3 精确读取仅支持日线，旧 V2 BarSeries GET 已删除；原接口在非 fixture 下也明确拒绝 1m。TickFlow 只有测试 fake intraday 无生产方法；Pytdx 为 5m 起步；官方 TdxAiData 分钟线独立归 R02.25/26 | blocked：尚无符合 1m Consumer 的生产 Provider；Risk/Automation 已前置返回不可用，不拿测试桩或旧缓存推断准入。未来来源须先固定 endpoint/1m/资产/口径/单位；见主仓分钟 Consumer 选择证据 |
| R02.16 | 继承 R02.15：CN STOCK × INTRADAY_BAR/1m → ThesisLedger 当前精确路由 | R02.15 尚无单一生产 Provider，故本适配叶不实施；既有 fixture 只验证合同形状，生产 1m 继续 fail-closed | blocked on R02.15；不得通过新增平行 Consumer、把 Pytdx 5m 冒充 1m 或把 Server 派生周期反向当源来关闭 |
| R02.17 | RQData `get_price` × CN ETF × HISTORICAL_BAR/1d/none → 既有 `MarketBarWindowReaderV3`；Owner：DSA 来源目录责任人 | 官方接口与当前代码只读核对，已锁定单一候选、显式窗口/口径及 Consumer；[证据](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-17-rqdata-etf-bar-selection.md)。账号隔离仅有事件接缝，Bar 路由未实现 | P02 已满足；G0-M 的真实行情权限、ETF 身份、量额单位/覆盖尚缺，故本叶未完成，R02.18 不启动生产适配 |
| R02.18 | 继承 R02.17 的 RQData `get_price` × CN ETF × 1d/none → 既有 `MarketBarWindowReaderV3`；Owner：单一 DSA adapter 路径 | 纯请求/响应[合同](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-18-rqdata-etf-daily-contract.md)与复用事件总期限机制的[进程读取](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-18-rqdata-etf-process.md)已在本地验证；无生产 Bar 库存 | 合同和进程子叶已完成；真实身份、权限、单位、历史覆盖、当前准入及 Reader 接线依赖 G0-M 与后继 runtime/target，父叶未完成 |
| R02.19 | 官方 TdxAiData × CN STOCK × DAILY_BAR/none → 一个现有历史 Bar Reader；Owner：DSA 来源目录责任人 | §2 官方 SDK/API 候选、原 R02.6 → 核实一个官方日线接口、SDK/授权/系统依赖及 Reader | P02、G0-M；确认官方身份、调用权限及目标架构前不安装/接入 |
| R02.20 | 继承 R02.19 唯一选择 × CN STOCK × DAILY_BAR/none → 指定 Bar Reader；Owner：单一 DSA adapter 路径 | R02.19 选择记录 → none 单口径适配与 Bar 定向测试 | 依赖 R02.19、C04、S03–S05、G0-M；独立证明单位、时间、窗口和 raw 语义 |
| R02.21 | 官方 TdxAiData × CN STOCK × DAILY_BAR/qfq → 一个现有历史 Bar Reader；Owner：DSA 来源目录责任人 | §2 TdxAiData qfq 候选 → 独立确认 qfq 基准、endpoint 与 Reader | P02、G0-M；不得从 none 接口推断 qfq 支持或基准 |
| R02.22 | 继承 R02.21 唯一选择 × CN STOCK × DAILY_BAR/qfq → 指定 Bar Reader；Owner：单一 DSA adapter 路径 | R02.21 选择记录 → qfq 适配与专属测试 | 依赖 R02.21、C04、S03–S05、G0-M；确认基准/窗口/修订再启用 |
| R02.23 | 官方 TdxAiData × CN STOCK × DAILY_BAR/hfq → 一个现有历史 Bar Reader；Owner：DSA 来源目录责任人 | §2 TdxAiData hfq 候选 → 独立确认 hfq 的查询窗口、基准与 Reader | P02、G0-M；查询窗口可能改变结果的风险必须有实测证据 |
| R02.24 | 继承 R02.23 唯一选择 × CN STOCK × DAILY_BAR/hfq → 指定 Bar Reader；Owner：单一 DSA adapter 路径 | R02.23 选择记录 → hfq 适配与专属测试 | 依赖 R02.23、C04、S03–S05、G0-M；不能用 qfq/none 测试替代 |
| R02.25 | 官方 TdxAiData × CN STOCK × INTRADAY_BAR → 一个现有分钟 Consumer；Owner：DSA 来源目录责任人 | §2 TdxAiData 分钟候选 → 选择一个 interval、接口、Reader 与许可/架构条件 | P02、G0-M；不把官方分钟协议与 Pytdx 社区协议混同 |
| R02.26 | 继承 R02.25 唯一选择 × CN STOCK × 单一 interval × INTRADAY_BAR → 指定 Consumer；Owner：单一 DSA adapter 路径 | R02.25 选择记录 → 一个 interval 的适配与边界测试 | 依赖 R02.25、C04、G0-M；不扩展 M1 日线回测范围 |
| R02.27 | 官方 TdxAiData × 单一选择资产 × POST_MARKET_PACKAGE → 一个现存 Consumer；Owner：DSA 来源目录责任人 | §2 官方盘后包候选 → 固定一个 SDK/包版本、资产、字段、周期、上游和 Reader；未发现 Consumer 则回填 Spec | P02、G0-M；先核系统库、授权、架构、字段和目标运行环境 |
| R02.28 | 继承 R02.27 唯一选择 × 单一资产/能力 × POST_MARKET_PACKAGE → 指定 Consumer；Owner：单一 DSA adapter 路径 | R02.27 选择记录 → 一个盘后包适配器与单测 | 依赖 R02.27、C04、G0-M；SDK 与目标镜像条件满足后才可实现 |
| R02.29 | 已选择 Tushare Pro/fund_daily × CN ETF × DAILY_BAR/1d/none → ThesisLedger V3 Bar Reader；Owner：DSA 来源目录责任人 | 复用 M24 的 endpoint、原始口径、量额单位和读取入口，不重复建立第二条适配 | P02、G0-M；选择已落实，Token/积分权限及目标 ETF 覆盖仍待真实确认 |
| R02.30 | 继承 R02.29：Tushare Pro/fund_daily × CN ETF × DAILY_BAR/1d/none → ThesisLedger V3 Bar Reader；Owner：TushareExactDailyMixin | 复用 M24-b1/b2 本地实现、分段/修订/准入/日历覆盖离线证据 | 本地适配已完成；C04、S03–S05、G0-M 的真实门禁仍待验收，不能从 STOCK 权限外推 |
| R02.31 | 选择：Tushare Pro × ETF × ADJUSTMENT_FACTOR → 一个现存事件/复权 Consumer；Owner：DSA 来源目录责任人 | §2 Tushare ETF 因子候选 → 确认 endpoint、因子锚点、频率/可见时间和单一 Consumer；无 Consumer 则回填 Spec | P02、G0-M；因子不等同完整公司行动事件表 |
| R02.32 | 继承 R02.31 唯一选择：Tushare Pro × ETF × ADJUSTMENT_FACTOR → 指定 Consumer；Owner：单一 DSA adapter 路径 | R02.31 选择记录 → 一个因子适配与锚点/日期测试 | 依赖 R02.31、C04、S03–S05、G0-M；Consumer、锚点或权限未确认前 blocked |

### R03 基金净值（6）

2026-09-27 日期与真实样本进展：共享日期校验及 API 时间排序修复完成，46 项定向测试通过，官方目标同步及实际最新/历史 HTTP 通过。000001 的 AKShare/Efinance 各返回 6,014 条并通过日期校验，实际 API 选中 AKShare。随后 Efinance 固定大页及 TLS 校验缺口已修复：新增同源有界 Reader，73 项相关测试通过，目标首次完成 7 页/6014 条及受控网关 HTTP 消费。两条实际 endpoint 同属 EastMoney；披露时间、版本修订、源端历史完整性与准入仍开放，不能由样本成功关闭 R03.1–R03.4。详见 `thesis-ledger-nav-source-evidence.md`。

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R03.1 | AKShare/EastMoney × MUTUAL_FUND × FUND_NAV → ThesisLedger NAV Reader；ProviderRuntime | §3 fund_open_fund_info_em 的最新净值消费、日期合同与目标 000001 样本已验证；[来源证据](thesis-ledger-nav-source-evidence.md)。新默认路由用 `akshare/eastmoney` | G0-M；净值日、披露/可见时间、单位净值与修订语义分别核对，旧 alias 尚未迁移 |
| R03.2 | AKShare/EastMoney fund_open_fund_info_em × MUTUAL_FUND × FUND_NAV_HISTORY → NAV History Reader；AkshareFetcher.get_fund_nav_history | §3 历史日期序列、目标样本及当前默认 source 已对账；[来源证据](thesis-ledger-nav-source-evidence.md) | G0-M；不可当盘中估值，更新/可见时间、修订和成立以来覆盖仍开放，旧 alias 尚未迁移 |
| R03.3 | Efinance adapter/EastMoney × MUTUAL_FUND × FUND_NAV → ThesisLedger NAV Reader；ProviderRuntime | §3 现有 EastMoney 分页 Reader 的最新净值消费、日期合同及目标 000001 样本已验证；[来源证据](thesis-ledger-nav-source-evidence.md) | G0-M；净值日不等于披露时刻，正式性、历史修订及来源完整性仍未通过 |
| R03.4 | Efinance adapter/EastMoney × MUTUAL_FUND × FUND_NAV_HISTORY → NAV History Reader；EfinanceFetcher.get_fund_nav_history | §3 独立有界 Reader 及目标 000001 七页/6014 行样本、升序归一已验证；[来源证据](thesis-ledger-nav-source-evidence.md) | G0-M；与 AKShare 均属 EastMoney，不算独立备用；公告可见性、源端修订和成立以来覆盖仍开放 |
| R03.5 | 来源待选 × MUTUAL_FUND × INTRADAY_ESTIMATE → 一个现存研究 Consumer；Owner：DSA 来源目录责任人 | 2026-09-28 再核：DSA/Agent/Server 当前没有场外基金盘中估值 Consumer 或 manifest；检索到的 valuation 均为股票基本面/组合估值，不满足本能力。按 Spec §3.3“只接现有消费入口”停止选择，不新建基金估值平台 | blocked：先由产品范围新增明确 Consumer、TTL/stale 与展示用途后才能重新选择来源；正式 NAV、成交 Bar 与回测执行价格不受影响 |
| R03.6 | 继承 R03.5 唯一选择 × MUTUAL_FUND × INTRADAY_ESTIMATE → 指定研究 Consumer；Owner：单一 DSA adapter 路径 | R03.5 已确认当前无现存 Consumer，因此本适配叶不实施；不得借股票 valuation、正式 NAV 或 Agent 文案创建影子消费链 | blocked on R03.5；未来若明确 Consumer，须独立定义来源、TTL/stale、缓存和不进入成交/回测的边界后再实施 |

### R04 基金资料与持仓（5）

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R04.1 | AKShare/EastMoney `fund_portfolio_hold_em` × MUTUAL_FUND × FUND_HOLDINGS → ThesisLedger Holdings Reader；AkshareFetcher.get_fund_holdings | 当前安装版源码确认 `date` 为查询年份、`占净值比例` 去 `%` 后为 0–100 数值；现有季度/代码/权重/合计/披露未知合同保留。新增来源年份 helper：上海时区选今年，只有空结果可回退上一年，非空错年/坏行立即拒绝；内部保留 `sourceEndpoint/sourceQueryYear/sourceObservedAt`，wire 仍用 `reportPeriod/disclosureDate/fetchedAt`。集成修前 1 failed/6 passed，最终相关 28 passed | P02、G0-M；真实权限、目标基金完整持仓覆盖、实际披露发布时间/修订与连续可用性仍待验证；抓取观察时间不得冒充披露时刻 |
| R04.2 | 来源待选 × MUTUAL_FUND × 单一 FUND_PROFILE 字段组 → 一个现存研究 Consumer；Owner：DSA 来源目录责任人 | 2026-09-28 再核：除基金持仓/NAV 外，当前 DSA/Agent/Server 没有独立 FUND_PROFILE Consumer；基金名称目录不等于资料消费。按 Spec §3.3 停止来源选择，不新建资料平台 | blocked：先由产品范围明确现存消费入口及字段组后再选 Provider/endpoint；不得把目录名称或持仓字段扩写成资料能力 |
| R04.3 | 继承 R04.2 唯一选择 × MUTUAL_FUND × 同一 FUND_PROFILE 字段组 → 指定 Consumer；Owner：单一 DSA adapter 路径 | R04.2 当前无 Consumer，因此适配叶不实施；不从 NAV/持仓或 Agent 文案建立影子资料链 | blocked on R04.2；未来恢复时须独立固定字段、报告/观察时间与来源身份 |
| R04.4 | 来源待选 × MUTUAL_FUND × FUND_ASSET_ALLOCATION → 一个现存研究 Consumer；Owner：DSA 来源目录责任人 | 2026-09-28 再核未发现资产配置 Consumer/API；现有 FUND_HOLDINGS 只承担持仓快照，不能自动升级为资产配置。按非目标约束停止选择 | blocked：产品先明确资产配置字段组和 Consumer；不得为完成 M3 新建基金资料平台 |
| R04.5 | 继承 R04.4 唯一选择 × MUTUAL_FUND × FUND_ASSET_ALLOCATION → 指定 Consumer；Owner：单一 DSA adapter 路径 | R04.4 当前无 Consumer，本适配叶不实施 | blocked on R04.4；未来须分别验证报告期、披露时点、抓取观察时间和来源，不借持仓能力外推 |

### R05 财务与估值（8）

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R05.1 | AKShare × CN STOCK × FINANCIALS → get_fundamental_context()/既有研究 Consumer；data_provider/fundamental_adapter.py | 2026-09-28 首选 stock_financial_abstract 已锁定发现；本机1.18.94以1次/0重试取得600004原文98期及80×100 SDK表，CNY有原文证据；当前矩阵首行误报已[失败关闭](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r05-financial-fail-closed.md)，无参默认股票候选已移除；完整数值映射仍缺单位定义/证券身份；见[原样合同](../../thesis-ledger/docs/tasks/evidence/2026-09-28-m3-r05-abstract-raw-contract-capture.md) | P02、G0-M；原样取得不等于准入，金额倍率/比例单位、响应身份、publish_date语义/可见时间及历史修订仍待验证；最新值不得进入严格历史回测 |
| R05.2 | AKShare × CN STOCK × VALUATION → get_fundamental_context()；Owner：DSA 估值原响应适配 | `stock_value_em` 已[单样本采集](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r05-valuation-raw-contract.md)；DSA [原响应读取器](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r05-valuation-reader.md)及[当前研究消费](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r05-valuation-consumer.md)本地接线通过，股票成功时标 `partial`、身份/交易日/观察分离，失败回退通用报价，报价价格仍供股息率 | P02、G0-M；官方市值单位元，原响应无披露/修订时刻，当前样本不证明其他证券/真实分页/延迟，本地消费不等于目标运行或历史 PIT 准入 |
| R05.3 | Yahoo/yfinance × US STOCK × FINANCIALS → get_fundamental_context()；Owner：DSA 来源目录责任人 | 首个本地字段组选季度利润表/同日现金流；[期间对齐](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r05-yahoo-period-alignment.md)与[去年同期日期核验](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r05-yahoo-yoy-period.md)已限制跨季度混值和位置假定，`.info` 仅为期间未知汇总；具体 Yahoo 底层 endpoint、条款、响应身份/原发布/修订仍未核实 | P02、G0-R；AAPL 合成 fixture 不是 US 实际来源准入，也不外推 HK/其他市场 |
| R05.4 | 继承 R05.3 US STOCK × FINANCIALS → 既有 get_fundamental_context()；Owner：单一 DSA adapter 路径 | 同期字段与期间/可见性元数据经 DSA 上下文离线消费通过；仅有本地 fixture，没有真实来源合同与目标运行 | 依赖 R05.3、C04、G0-R；不声称历史 PIT 能力 |
| R05.5 | Yahoo/yfinance 1.7.0 × US STOCK × VALUATION → get_fundamental_context()；Owner：YfinanceFundamentalAdapter | 已选择 `Ticker.get_info()` 的 `trailingPE/priceToBook` 两个无量纲倍数；当前包静态核心入口为 `query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}`。仅 US STOCK；`AAPL.US` 归一到同一身份，HK/其他市场不继承。输出 current observation：`ratio_unit=multiple`、`currency=null`、本地 observed_at、source_available_at=null、historical_visibility_verified=false，不纳入 marketCap | 本地选择完成；真实 Yahoo 响应/许可/连续可用性、发布/修订时刻和 G0-R 未通过，不得作为历史 PIT；见主仓 Yahoo valuation 证据 |
| R05.6 | 继承 R05.5：Yahoo/yfinance × US STOCK × VALUATION → 现有 get_fundamental_context() | YfinanceFundamentalAdapter 生成独立 valuation bundle；Manager 在 US 且 bundle 有 PE/PB 时优先消费该 bundle，而不是可能混合 Longbridge/Finnhub/AlphaVantage 补字段的通用 quote valuation。两项齐全 ok、单项 partial；其他市场/无 bundle 保持原路径。Adapter/Consumer 两文件组 38 项通过 | 本地 Consumer 接线完成；真实 G0-R/目标运行仍开放，current observation 不提升为历史可见事实 |
| R05.7 | HiThink × 单一待选资产 × RESEARCH_DATA/单一字段组 → 一个现存 Research Consumer；Owner：DSA 来源目录责任人 | §2 HiThink research data 候选 → 选择一个资产、字段粒度、endpoint、可见时间和 Consumer | P02、G0-H；字段/资产/Consumer 未选前不可执行 |
| R05.8 | 继承 R05.7 唯一选择 × 同一资产/字段组 × RESEARCH_DATA → 指定 Consumer；Owner：单一 DSA adapter 路径 | R05.7 选择记录 → 一个研究数据适配与字段/时间测试 | 依赖 R05.7、C04、G0-H；不得由自然语言结果补成确定性行情/事件事实 |

### R06 指数板块与资金流（23）

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R06.1 | Efinance 0.5.9 / EastMoney 全市场股票快照 × CN STOCK × MARKET_BREADTH → MarketAnalyzer；Owner：EfinanceFetcher/DataFetcherManager | 选择现有 `get_market_stats` Consumer；当前静态底层为 `http://push2.eastmoney.com/api/qt/clist/get`。本叶只固定上涨/下跌/平盘/涨停/跌停家数，要求六位股票代码唯一；成功保留 `source=efinance/eastmoney:get_realtime_quotes(stock)`、本地 observed_at，source_available_at=null。`total_amount` 仅兼容保留，不纳入本叶单位证明 | 本地选择完成；真实源端时点、交易阶段、连续覆盖、成交额单位、HTTP/许可及 G0-M 仍开放，不把抓取时刻当历史 as-of；见主仓 market breadth 证据 |
| R06.2 | 继承 R06.1：Efinance/EastMoney × CN STOCK × MARKET_BREADTH → `EfinanceFetcher.get_market_stats` / MarketAnalyzer | 缺身份列、坏/重复股票代码整体拒绝，避免宽度重复计数；原涨跌停算法和 `total_amount` 兼容字段不变。专属修前2 failed/1 passed→3 passed，MarketAnalyzer/TickFlow回退组合21项通过；contract=`cn-stock-breadth-v1`、historical_visibility_verified=false | 本地 adapter/Consumer 合同完成；真实 G0-M、源端 as-of/单位/覆盖及目标运行仍开放，不能用于严格历史市场宽度 |
| R06.3 | AKShare/EastMoney `stock_board_industry_name_em` × CN 行业板块 × SECTOR_RANKING → MarketAnalyzer；AkshareFetcher.get_sector_rankings | 当前安装版静态链为 `https://17.push2.eastmoney.com/api/qt/clist/get`；新增纯合同校验板块名唯一/非空、涨跌幅可用，返回行显式 `source=akshare/eastmoney:stock_board_industry_name_em`。EastMoney 合同异常可进入既有 Sina 回退，但不会把回退结果冒充东财；本地红例 3 failed 后修复，专属7项、相邻消费者合并56项通过 | P02、G0-M；上游分类版本、真实快照时点/as-of、单位/覆盖与在线准入仍待验证；不因本地 source 标记宣称实时可用 |
| R06.4 | AKShare/Sina `stock_sector_spot(indicator=行业)` × CN 行业板块 × SECTOR_RANKING → 同一 MarketAnalyzer；同上 | 当前安装版静态链为 `http://money.finance.sina.com.cn/q/view/newFLJK.php?param=industry`；既有第二顺位回退保持，返回行显式 `source=akshare/sina:stock_sector_spot`，同一板块身份/涨跌幅合同复用。EastMoney 坏合同后的 Sina 成功明确标记 Sina，不隐藏实际来源；专属及相邻回归同 R06.3 | P02、G0-M；Sina HTTP 传输、分类口径版本、来源时点/单位/覆盖与真实准入仍开放；不能与 EastMoney 包装计为同源或无条件兼容备用 |
| R06.5 | Efinance/EastMoney `get_realtime_quotes(['行业板块'])` × CN 行业板块 × SECTOR_RANKING → MarketAnalyzer；EfinanceFetcher.get_sector_rankings | 当前安装版 efinance 0.5.9 静态链将行业板块 FS 传给 `http://push2.eastmoney.com/api/qt/clist/get`；复用同一纯合同校验行业名唯一/非空及有限涨跌幅，保留中文/兼容列并返回 `source=efinance/eastmoney:get_realtime_quotes(行业板块)`。专属 2 failed/1 passed → 3 passed，板块消费者合并 61 passed | P02、G0-M；真实传输、分类版本、来源时点/as-of、单位/覆盖和准入仍开放；与 AKShare/EastMoney R06.3 同源，不得计为独立备用 |
| R06.6 | Tushare `moneyflow_ind_ths` × CN 行业板块 × SECTOR_RANKING → MarketAnalyzer；TushareFetcher.get_sector_rankings | 复用统一排名合同，THS 行返回 `source=tushare/ths:moneyflow_ind_ths`，行业名唯一/非空、`pct_change` 有限；15:30 前通过现有交易日历选上一交易日，15:00 专属回归确认请求 `trade_date=前一交易日`，16:00 使用当日。与东财 fallback 保持不同 source/分类 | P02、G0-M；真实 Token/积分权限、源端发布时间、分类版本、单位/覆盖与准入仍待核，不由本地时间选择推断真实可见时刻 |
| R06.7 | Tushare `moneyflow_ind_dc` × CN 行业板块 × SECTOR_RANKING → MarketAnalyzer；同上 | THS 合同失败后沿既有第二顺位调用，同一 `trade_date`，只消费 `content_type=行业`，返回 `source=tushare/eastmoney:moneyflow_ind_dc`；重复/坏行业身份失败关闭。THS 重复身份→东财 fallback 专属红例修复后通过，完整 Tushare follow-up 9 项通过 | P02、G0-M；真实权限、东财分类版本、来源时点/单位/覆盖仍开放；THS/东财分类不得合并成同一口径或无条件兼容备用 |
| R06.8 | TickFlow SDK `universes.list/batch + quotes.get` × CN 行业板块 SW1 × SECTOR_RANKING → MarketAnalyzer | 既有 SW1 universe 合并与成分涨跌幅均值保留；新增 provider timestamp 横截面合同，参与排名的 quote 必须全部有同一规范时点，否则整体拒绝。成功行保留 `source=tickflow/sw1:universes.list+universes.batch+quotes.get`、`classification=SW1`、`classification_version=null`、provider `as_of` 与成分数；混合时点红例修复后通过，相关组合 89 项通过 | P02、G0-M；真实 SDK/套餐权限、SW1 分类版本、provider timestamp 源端语义、单位/覆盖和目标运行仍待核；不从 universe ID 猜版本 |
| R06.9 | AKShare/EastMoney `stock_board_concept_name_em` × CN 概念板块 × CONCEPT_RANKING → DataFetcherManager/MarketAnalyzer | 当前安装版静态链指向 EastMoney 概念板块页，并由 `_fetch_stock_board_concept_name_em` 在 `79.push2/17.push2/push2.eastmoney.com/api/qt/clist/get` 间有界选择；复用行业纯合同校验概念名唯一/非空与有限涨跌幅，返回 `source=akshare/eastmoney:stock_board_concept_name_em`。专属两红例修复后通过，板块/基本面相邻合并 58 项通过 | P02、G0-M；真实 endpoint 命中、分类版本、来源时点/as-of、单位/覆盖与在线准入仍开放；不得与行业排名合并，也不能因同属 EastMoney 就互相外推覆盖 |
| R06.10 | AKShare/EastMoney 行业板块成员 × CN STOCK × SECTOR_MEMBERSHIP → Screening industry enrichment；Owner：`src/services/screening/industry.py` | 已选现有唯一明确 Consumer：`enrich_industry_concepts`；可选 `provider=akshare` 时由 `stock_board_industry_name_em → stock_board_industry_cons_em` 生成当前代码→行业映射，并与稳定文件映射共用消费面。不另建 MarketAnalyzer/Agent 第二套 membership | 选择完成；当前只作为研究/筛选 enrichment。来源截面日期、分类版本、历史有效期及真实 G0-M 未证明，不能进入历史回测/PIT；见主仓 R06 membership 证据 |
| R06.11 | 继承 R06.10：AKShare/EastMoney × CN STOCK × SECTOR_MEMBERSHIP → Screening industry enrichment | 当前 provider cache 只保存本地 `created_at`/mtime（TTL）与 mapping，没有来源 `as_of`、分类版本或历史有效期；这些本地时间不得冒充板块截面日期。现有代码可作当前研究 enrichment，不能形成历史 membership 事实 | blocked：需来源截面日期/分类版本合同后再适配和测试；在此之前不新增猜测字段、不回填历史、不授 G0-M/PIT 准入；见主仓 R06 membership 证据 |
| R06.12 | AKShare stock_individual_fund_flow × CN STOCK × CAPITAL_FLOW/最新可用日 → get_capital_flow_context()及get_fundamental_context()；AkshareFundamentalAdapter.get_capital_flow | §4 最新日字段 → [精确请求](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m3-flow-request-scope.md)、[身份传递/缓存隔离](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m3-flow-scope-consumer.md)及[唯一日期/精确净额列](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-flow-latest-day.md)本地通过；相邻 44 项回归 | 两次官方核对仍缺金额单位/完整样本，完整数值合同skip；算法、原响应身份、P02/G0-M真实准入仍未通过 |
| R06.13 | 同一 stock_individual_fund_flow × CN STOCK × CAPITAL_FLOW/5 日 → 同一 Consumer；同上 | 安装版日表无 5 日累计列；`inflow_5d` 保持未知 | 此接口该能力 unavailable；新接口/累计算法/单位/时点需独立合同和 G0-M |
| R06.14 | 同一 stock_individual_fund_flow × CN STOCK × CAPITAL_FLOW/10 日 → 同一 Consumer；同上 | 安装版日表无 10 日累计列；`inflow_10d` 保持未知 | 此接口该能力 unavailable；不从 5 日或每日值推导，待独立合同和 G0-M |
| R06.15 | AKShare stock_main_fund_flow × CN STOCK 排名集合 × CAPITAL_FLOW_RANKING；原拟单股净额回退不成立 | [候选失配证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-main-flow-candidate.md)：仅全市场类别入参，输出今日净占比/排名/涨跌幅；当前 Consumer 未调用 | 原单股净额候选 unavailable；如需备用，重新选择接口和合同，P02/G0-M 未通过 |
| R06.16 | 同一 stock_main_fund_flow × CN STOCK 排名集合 × 5 日排行榜百分比 | §4 无等价 5 日净额列，`inflow_5d` 保持未知 | 原净额候选 unavailable；不把净占比当金额 |
| R06.17 | 同一 stock_main_fund_flow × CN STOCK 排名集合 × 10 日排行榜百分比 | §4 无等价 10 日净额列，`inflow_10d` 保持未知 | 原净额候选 unavailable；不把净占比当金额 |
| R06.18 | AKShare stock_sector_fund_flow_rank × CN 行业板块 × CAPITAL_FLOW/当前排名 → get_capital_flow_context() 当前研究块 | [单 endpoint 本地证据](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-sector-flow-rank.md)：显式今日/行业参数、精确净额列、坏形状拒绝、48 项相邻回归 | 金额单位、来源时刻、实际行业覆盖及 P02/G0-M 仍未通过；不能历史复用 |
| R06.19 | AKShare stock_sector_fund_flow_summary × 指定行业的 CN STOCK 集合 × 个股资金流 | 安装版与官方均为给定行业的成员个股表，不是跨行业排名；旧无参回退已移除；[Consumer 核对](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-sector-member-consumer.md)确认当前仅有股票代码及跨行业排名入口 | 当前接线 blocked：需独立行业标识/成员和明确 Consumer，跳过默认“电源设备”；P02/G0-M 未通过，不计双源 |
| R06.20 | 单一现存 CN STOCK × DRAGON_TIGER_EVENT_INDEX 历史 Consumer 选择；Owner：DSA 来源目录责任人 | [现存入口核对](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-r06-dragon-consumer.md)：当前研究/Agent/筛选读取无历史截点的基本面块，Server 回测不读该块；未找到所需历史 Consumer | 选择结果 blocked；已回填 Spec，新增历史入口及事件/披露可见性合同前不实施 R06.21–23 |
| R06.21 | AKShare stock_lhb_stock_statistic_em × CN STOCK × DRAGON_TIGER_EVENT_INDEX → 待确定历史 Consumer | 当前只在无历史截点的 get_dragon_tiger_flag() 候选链中；不能用本机窗口补历史时间 | 依赖 R06.20、P02、G0-M；历史适配 blocked |
| R06.22 | AKShare stock_lhb_detail_em × CN STOCK × DRAGON_TIGER_EVENT_INDEX → 待确定历史 Consumer | 当前同一候选链，未证明披露时刻及独立 source 选择 | 依赖 R06.20、P02、G0-M；历史适配 blocked |
| R06.23 | AKShare stock_lhb_jgmmtj_em × CN STOCK × DRAGON_TIGER_EVENT_INDEX → 待确定历史 Consumer | 当前同一候选链，未证明事件/披露时间及历史用途 | 依赖 R06.20、P02、G0-M；历史适配 blocked |

### R07 公告与资讯辅助（27）

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R07.1 | AKShare fund_cf_em × ETF × SPLIT_EVENT → 现有公司行动依赖 Reader；AkshareFundamentalAdapter.get_corporate_actions_v2 | §2/§4 ETF 拆分候选 → 单 endpoint 的 ratio/effective date 映射、source 字段和测试 | P02、G0-M、M22/M23；公告索引不自动等于 event fact；未知 ratio/日期时 fail closed |
| R07.2 | AKShare/endpoint 待选 × ETF × CASH_DISTRIBUTION → 一个现存公司行动 Reader；Owner：DSA 来源目录责任人 | §2 ETF 现金分配候选 → 锁定一个接口、现金单位、日期语义与 Consumer | P02、G0-M、M22/M23；未确认 endpoint/消费者前只交付 blocked 结论 |
| R07.3 | 继承 R07.2 唯一选择 × ETF × CASH_DISTRIBUTION → 指定 Reader；Owner：单一 DSA adapter 路径 | R07.2 选择记录 → 一个现金分配适配及单位/日期测试 | 依赖 R07.2、C04、G0-M、M22/M23；分红事实字段满足契约后才可用 |
| R07.4 | AKShare fund_announcement_dividend_em × ETF × CORPORATE_ACTION_NOTICE → 公司行动依赖 Reader；AkshareFundamentalAdapter | §4 ETF 公告索引行 → 单独的 notice 适配/来源时间测试 | P02、G0-M；公告日/可见日未知需显式保留；notice 不能冒充 SPLIT_EVENT |
| R07.5 | AKShare stock_fhps_detail_em × CN STOCK × CORPORATE_ACTION_CANDIDATE → 公司行动依赖 Reader；同上 | §4 股票候选一 → 单 endpoint 的源身份、事件字段与测试/不可用结论 | P02、G0-M、M22/M23；运行时来源字段证明候选选择；不与另外两个候选计双源 |
| R07.6 | AKShare stock_history_dividend_detail × CN STOCK × CORPORATE_ACTION_CANDIDATE → 同一 Reader；同上 | §4 股票候选二 → 独立 endpoint 的源身份/事件字段验证 | P02、G0-M、M22/M23；接口完整性与日期语义未知保持未知 |
| R07.7 | AKShare stock_dividend_cninfo × CN STOCK × CORPORATE_ACTION_CANDIDATE → 同一 Reader；同上 | §4 CNINFO 候选三 → 单 endpoint 的真实 upstream/许可/字段和测试 | P02、G0-M、M22/M23；不得因 AKShare 包装名视为独立备用 |
| R07.8/{sourceId} | 每一个用户显式配置的 RSS/Atom URL × NEWS_ITEM × NEWS_INDEX → IntelligenceService；Owner：src/services/intelligence_service.py | §4 配置源汇总行 → 对一个 URL 单独生成稳定 source key、时间字段/解析版本/许可记录及测试；无启用 URL 时结果为 N/A | P02、G0-R；必须尊重显式启用；feed 发布时间与抓取时间分开 |
| R07.9 | NewsNow connector/CLS × NEWS_ITEM × NEWS_INDEX → IntelligenceService | §4 source id=CLS → 单 source id 的实例身份、payload 日期/抓取时间和解析测试 | P02、G0-R；当前公开实例的授权/上游来源未确认，不作行情/事件事实 |
| R07.10 | NewsNow connector/Xueqiu × NEWS_ITEM × NEWS_INDEX → IntelligenceService | §4 source id=Xueqiu → 单 source id 的来源/时间字段及解析测试 | P02、G0-R；上游发布方未知，用户显式开启后才请求 |
| R07.11 | NewsNow connector/WallstreetCN × NEWS_ITEM × NEWS_INDEX → IntelligenceService | §4 source id=WallstreetCN → 单 source id 的来源/时间字段及解析测试 | P02、G0-R；不得将新闻内容转为行情事实 |
| R07.12 | NewsNow connector/Jin10 × NEWS_ITEM × NEWS_INDEX → IntelligenceService | §4 source id=Jin10 → 单 source id 的来源/时间字段及解析测试 | P02、G0-R；实例授权、原始来源与可见时间待核 |
| R07.13 | NewsNow connector/Gelonghui × NEWS_ITEM × NEWS_INDEX → IntelligenceService | §4 source id=Gelonghui → 单 source id 的来源/时间字段及解析测试 | P02、G0-R；不与其他 source id 合并为一个已确认上游 |
| R07.14 | a-stock-data v3.10.0/Tencent × CN STOCK × REALTIME_QUOTE → 既有 AkshareFetcher 腾讯报价 Consumer；Owner：DSA 来源目录责任人 | 固定提交 `2e0ae63`、Apache-2.0 代码许可及 `qt.gtimg.cn/q=`；与现有直接请求同源，选择结果为不新增 Provider；[只读审查](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-g0-r-auxiliary-source-audit.md) | 来源选择完成；G0-R 的真实条款/响应准入仍开放；R07.15 不重复适配 |
| R07.15 | 继承 R07.14 唯一选择 × 同一资产 × 单一行情能力 → 指定 Consumer；Owner：单一 DSA adapter 路径 | R07.14 选择记录 → 一个来源适配/测试 | 依赖 R07.14、C04、G0-R；实际 upstream 需与现有腾讯入口去重 |
| R07.16 | a-stock-data 固定版本/Tdx 盘后包 × 单一选定资产 × POST_MARKET_PACKAGE → 一个现存 Consumer；Owner：DSA 来源目录责任人 | §2 通达信盘后包候选 → 固定一个代码版本、真实 endpoint、字段、资产和 Reader | P02、G0-R；不把候选代码名称当 Provider |
| R07.17 | 继承 R07.16 唯一选择 × 同一资产 × POST_MARKET_PACKAGE → 指定 Consumer；Owner：单一 DSA adapter 路径 | R07.16 选择记录 → 一个盘后包适配/测试 | 依赖 R07.16、C04、G0-R；无现存 Consumer 即停止 |
| R07.18 | a-stock-data 固定版本/公告接口 × 单一选定资产 × ANNOUNCEMENT_INDEX → 一个现存 Consumer；Owner：DSA 来源目录责任人 | §2 公告接口候选 → 固定版本、服务身份、公告日/可见时间和单一 Consumer | P02、G0-R；许可/远端身份未核不调用 |
| R07.19 | 继承 R07.18 唯一选择 × 同一资产 × ANNOUNCEMENT_INDEX → 指定 Consumer；Owner：单一 DSA adapter 路径 | R07.18 选择记录 → 一个公告索引适配/测试 | 依赖 R07.18、C04、G0-R；公告索引不得冒充公司行动事实 |
| R07.20 | 问财/Skill 底层服务 × 单一资产待选 × RESEARCH_RETRIEVAL → 一个现存检索 Consumer；Owner：DSA 来源目录责任人 | §2 底层检索候选 → 确认 SDK/服务/endpoint/许可、一个字段组与 Consumer；否则 blocked | P02、G0-R；自然语言结果不能接行情/公司行动确定性链路 |
| R07.21 | 继承 R07.20 唯一选择 × 同一资产/字段组 × RESEARCH_RETRIEVAL → 指定 Consumer；Owner：单一 DSA adapter 路径 | R07.20 选择记录 → 一个检索能力适配/测试 | 依赖 R07.20、C04、G0-R；未核身份/Consumer 时不实施 |
| R07.22 | 问财/Skill 底层服务 × 单一资产待选 × ANNOUNCEMENT_LOOKUP → 一个现存公告检索 Consumer；Owner：DSA 来源目录责任人 | §2 底层公告查找候选 → 锁定一个接口、公告源身份、时间字段和 Consumer | P02、G0-R；不能把检索摘要转换成已核验事件事实 |
| R07.23 | 继承 R07.22 唯一选择 × 同一资产 × ANNOUNCEMENT_LOOKUP → 指定 Consumer；Owner：单一 DSA adapter 路径 | R07.22 选择记录 → 一个公告检索适配/测试 | 依赖 R07.22、C04、G0-R；身份或可见时间不足则保持 blocked |
| R07.24 | Tushare Pro/endpoint 待核 × MUTUAL_FUND × CASH_DISTRIBUTION → 一个现存公司行动/NAV Consumer；Owner：DSA 来源目录责任人 | §2 Tushare 基金分红候选 → 选择 endpoint、一个既有 Consumer、日期字段/权限；无 Consumer 回填 Spec | P02、G0-M；Token/积分权限与除息/登记/派息语义逐项确认 |
| R07.25 | RQData/fund.get_split × CN ETF × SPLIT_EVENT → 既有 Server 公司行动/Backtest Snapshot 消费者；Owner：DSA 来源目录责任人 | 复用 M29 的唯一精确入口；身份原文、当前准入/账号修订、期限隔离及生产事件入口已接线，比例及经济生效日独立标准化；本地与目标拒绝验证见 [RQData 生产接线证据](../../thesis-ledger/docs/tasks/evidence/2026-09-27-rqdata-event-runtime.md) | G0-M；真实目标身份、接口权限、完整历史与目标正向验收仍开放，缺公告可见性不能产生事件信号 |
| R07.26 | RQData/fund.get_dividend × CN ETF × CASH_DISTRIBUTION → 既有 Server 公司行动/Backtest Snapshot 消费者；Owner：DSA 来源目录责任人 | 复用 M30 和 M29 的唯一事件入口；独立币种原文校验、每份税前现金及登记/除息/派息日期已接生产入口；本地与目标拒绝验证见 [RQData 生产接线证据](../../thesis-ledger/docs/tasks/evidence/2026-09-27-rqdata-event-runtime.md) | G0-M；真实币种/权限、完整历史和目标正向验收仍开放，与 R02.17 历史 Bar 分别验收 |
| R07.27 | 官方 TdxAiData/endpoint 待核 × 一个选择资产 × CORPORATE_ACTION/RIGHTS → 一个现存公司行动 Consumer；Owner：DSA 来源目录责任人 | §2 官方权息候选 → 选择一个资产、事件类型映射、SDK/授权与 Consumer | P02、G0-M；事件种类/日期/许可未锁时 blocked；不与 Pytdx 或 a-stock-data 混同 |

### R08 可选本地镜像（4）

| 子任务 | 唯一叶子与 DSA Owner | 输入 → 叶子输出 | 依赖与通过门禁 |
| --- | --- | --- | --- |
| R08.1 | free-stockdb 镜像准入选择叶；Owner：DSA 来源目录责任人/提供镜像的维护者 | §2/§4 镜像未知记录 → 由维护者提供一个精确镜像位置；核实来源链、许可、可达性和覆盖，缺关键凭据输出 unavailable | P02、G0-R；不得自行寻找/下载未知数据集，不提供可信镜像则保持关闭 |
| R08.2 | R08.1 通过后选择一个 Provider × 资产 × 能力 × 口径 × Reader；Owner：DSA 来源目录责任人 | 已通过的一个镜像及现存研究 Consumer → 输出单一能力契约、source fingerprint 与窄写入路径；无 Consumer 则回填范围 | 依赖 R08.1、P02、G0-R；本叶只准入一个能力，不一次纳入整库 |
| R08.3 | 继承 R08.2 唯一选择 × 一个 Reader → 只读镜像适配；Owner：单一 DSA adapter 路径 | R08.2 选择记录 → 一个只读 Reader、来源指纹/空值/重复测试 | 依赖 R08.2、C04、G0-R；与 PostgreSQL 主库及回测冻结快照隔离 |
| R08.4 | 继承 R08.2 唯一选择 × 一个受控导入入口；Owner：单一 DSA 导入路径 | R08.2 选择记录 → 一个显式范围/只读源/幂等导入任务及测试 | 依赖 R08.2、C04、G0-R；不得替换 PostgreSQL/快照，不可与 R08.3 合并验收 |

### P02-c 展开自检

- 原 29 条建议均映射到 116 个固定叶子及一个按显式 RSS/Atom URL 展开的 sourceId 模板；实际叶子数为 116 加已登记 URL 数。多资产、多个 endpoint、不同能力/周期和不同 Consumer 已分开编号。R02.11–R02.14 独占指数报价，R06 不重复建立 INDEX_QUOTE 任务。
- 未知接口与缺失 Consumer 先通过“选择”叶确定一个来源链/Consumer；适配后继显式依赖选择叶。没有选择结论的后继保持 blocked；没有现存 Consumer 的分支须回填 Spec，不得创建平行 Consumer。
- AKShare/Efinance 的 EastMoney 上游、AKShare Tencent/TencentFetcher、NewsNow 实例及其多个 source id、官方 TdxAiData 与社区 Pytdx、a-stock-data 中的候选实现均保留各自身份和待核关系，不预记为独立备用。
- 本节的 117 个编号条目保留原计划编号，已有实现与局部门禁结果按叶子证据更新；R07.8 仅在存在明确启用的配置 URL 时物化为单 URL 叶子。P02-c 展开时及本次状态文档对齐均没有调用 Provider、读取凭据值、下载 SDK/数据、修改 manifest/代码或执行叶子实现。后续实施证据独立记录；G0、定向产品测试、Consumer/UI、数据库、Docker、浏览器、AI 和部署门禁仍须各自留在对应实施任务中。
## 6. P02-b 自检

- Spec 来源范围核对：§4.1 的九组来源均有映射（HiThink、AKShare/EastMoney、Tushare、TdxAiData、RQData、现有腾讯/Efinance/BaoStock、a-stock-data、free-stockdb、问财/Skill）；§4.2 五类能力均已覆盖；§14.1 的 HiThink、raw/hfq 来源、官方 TdxAiData、free-stockdb 四项外部前提均保留为独立准入条件。
- P02-b 初次自检时的目录拆分为 §2 的 34 条 Spec 候选行、§3 的 57 条 manifest 原子行及 §4 的 38 条 manifest 外实现/来源选择行；当时的标签去重统计不随增量自动更新。
- 当前表格逐行重计为 §2 的 35 条、§3 的 67 条、§4 的 38 条；manifest 当前仍为 15 个 Provider × source ID。§2/§4 未知或来源选择标签不表示真实 Provider/endpoint 个数；行数增加不代表真实来源准入。
- 原子行字段：每条 §3/§4 行都写 Provider、实际上游/endpoint、一个资产、一个能力及一个周期/口径；未知项明确未知。§2 的来源选择行允许资产、endpoint 或能力未知，但明确标为不可执行。
- 上述数量是静态登记和字面标签统计，不是权限、覆盖或真实可用证据。
- 同源关系：AKShare/Efinance EastMoney、AKShare Tencent/TencentFetcher、pytdx/官方 TdxAiData、NewsNow 实例/聚合来源仍分别标识；无法根据包装名推定实际上游的行保留未知，不算独立备用。
- R01–R08 的原 29 项建议作为 P02-c 输入，已在 §5 展开为 116 个固定编号叶子及按配置 URL 物化的 R07.8 模板。仍标“待选择”的 endpoint、Consumer 与来源身份由对应选择叶核实，不能据目录登记宣称适配或准入完成。
- 本轮未读取凭据值、请求 Provider、下载 SDK 或改 manifest/代码。
