# ThesisLedger 当前契约与版本入口

DSA Fork 通过独立的 ThesisLedger 路由向同级主仓提供行情和量化能力。这些路由不改变 DSA 原生 `/api/v1` 接口，也不依赖管理员 session。

当前依赖事实和可交易性来源分别由 `src/services/thesis_ledger_dependency_facts.py`、`src/services/thesis_ledger_tradability_provider.py` 拥有，当前响应使用合同版本 1。旧模块路径、转导别名及无消费者的旧 Bar 夹具已删除；原生来源单位格式、映射证据和不可变修订标识继续校验。主仓消费者与最终目标验收见相邻仓 `docs/tasks/evidence/2026-10-02-canonical-final-completion.md`。

## 版本与部署边界

本页记录当前源码的版本入口。Control 写入使用 dsa-control-v1 路径与信封。

| 范围 | 源码入口 | 约束 |
| --- | --- | --- |
| dsa-data-v1 | `POST /api/v1/thesis-ledger/market/bars` | 精确路由、有效价格与调用模式对应的窗口校验；基础价格配置与高级能力准入分别判定 |
| 指标计算 | `POST /api/v1/thesis-ledger/market/indicators/calculate` | 纯计算要求 `contractVersion: 1`，校验完整输入指纹 |
| 交互图表 | `POST /api/v1/thesis-ledger/market/chart-bars` | 可含未收盘条目，不提供回测覆盖证明 |
| 事件 | `POST /api/v1/thesis-ledger/market/events` | 独立事件请求契约，不由价格序列推断事件完整性 |
| dsa-control-v1 | `/api/v1/thesis-ledger/control/*` | 握手、Provider、策略、路由能力、Catalog Job 与 ACK 使用独立 Control Token；写入使用当前 Control 信封 |
| 标的目录 | `/api/v1/thesis-ledger/catalog/*` | 快照和增量使用 Data Token、版本 1 与 generation/checksum/cursor |

ThesisLedger 独立入口由 `api/data_app.py` 挂载。Control 握手要求独立 Control Token，信封包含 `contractVersion: 1`、`consumer: "thesis-ledger"`、非空 `requestId` 和 `contract: "dsa-control-v1"`。`GET /api/v1/thesis-ledger/capabilities` 声明支持的数据协议版本，不代表任何真实来源已准入。

infra 的 `compatibility.json` 现以单一 `dsa.dataContract=dsa-data-v1`、`dsa.controlContract=dsa-control-v1` 组合及协议/正向业务 smoke 作为发布要求。协议 smoke 成功不能读作真实行情、回测或 AI 验收成功；正向业务 smoke 仍不能替代 Server/Worker 与客户端链路。2026-10-03 已通过官方代码同步完成当前目标更新，真实普通回测与冻结重放通过；见主仓 `docs/tasks/evidence/2026-10-03-common-price-baseline.md`。代码同步只更新容器可写层，不构成新镜像发布。

基础 ETF 日线 `hithink/fund-market-historical/qfq` 与 `tencent/tencent/none,qfq,hfq` 按适配能力、启用状态、必要凭据和精确路由就绪，取消逐标的、逐窗口和短期有效的人工 RouteAdmission 前置。公共层从实际 Bar 与日历整理日级状态；缺日按显式研究假设不交易。以下“准入”要求继续适用于其他高级能力，不能重新成为上述基础价格路径的申请手续。

## 多窗口完整行情

`GET /api/v1/thesis-ledger/capabilities` 的 `multiWindowProtocols` 声明 `market-multi-window-content-v1`。该字段表示响应协议支持，不授予任何来源权限；消费者收到多窗口响应时必须同时确认 dsa-data-v1 和该协议，缺少声明或不认识协议时拒绝解析为完整行情。

当前多窗口调度用于固定 `hithink / fund-market-historical` 目标的 ETF、qfq、1d 请求。超出单窗五个日历年时，先基于已核验的交易日历规划全部子窗口，相邻窗口必须有上市后真实交易日交集。每窗最多五年，最多八次请求，共享 4.5 秒总预算；每窗重新经过生产运行时的策略、目标和准入检查。子窗失败、无覆盖、交集价格冲突或超时即停止，不以部分数据构造成功响应。

父响应保留完整 `windowObservations`，每项包含带时区的 `startedAt`、`completedAt` 和严格单窗口 `response`，禁止递归嵌套。子响应观测时间不得晚于采集完成时间；父观测时间取最晚子完成时间。重叠行情的 OHLC、量额和收盘状态必须一致，可用时间取较晚值。父响应 Bars 必须等于子窗口去重并集，完整交易日覆盖另行校验。

父价格基准保留 `request-window` 作用域与 `local-observation` 修订，不把交集一致解释为已知上游算法或全局基准。`inputFingerprint` 和价格基准修订的 `contentHash` 均使用跨语言协议 `market-multi-window-content-v1`：覆盖全部子响应和观测时间，排除传输请求 ID 与父哈希自引用。消费者必须验证完整内容指纹，不能删除子观测后按旧单窗协议接受。

Server 冻结及 Snapshot 消费端保留完整观测并离线校验；接口能力与本地合同测试不替代真实来源、数据库冻结及回测联通验收。旧单窗口仍使用原响应格式，图表读取维持下节的独立契约。

## 图表交互读取

`POST /api/v1/thesis-ledger/market/chart-bars` 使用与行情契约一致的 Bearer Token，当前支持已配置且符合对应能力条件的中国市场日线精确路由。请求除日期窗口、证券和 RouteKey 外，还须携带 `purpose: "interactive-chart"` 和固定 `routeTarget`（来源、上游、顺序）。每次请求经过运行时有效策略、必要凭据及能力检查，只调用固定目标；基础价格不检查人工窗口准入。

响应保留来源价格事实、实际来源和逐条收盘状态，`availableAt` 为真实观测时间；可返回盘中未收盘日线。实际覆盖边界和最近完整交易日从本次返回的数据计算。该端点没有 `coverageProof`，不得用作完整回测窗口或历史可见性证明；回测使用 `POST /api/v1/thesis-ledger/market/bars` 的严格完整窗口校验。上游、输入或口径失败返回安全的错误信封，不泄漏原始响应。目标运行态及真实来源验证独立执行，本地夹具不代表准入已完成。

## 基金净值当前路由

`GET /api/v1/thesis-ledger/market/fund-nav?symbol=000001.OF` 和 `GET /api/v1/thesis-ledger/market/fund-nav/history?symbol=000001.OF&start=...&end=...&limit=365` 使用 Data Bearer Token。最新点和历史点均返回 `version: 1`、规范化 `.OF` 代码、单位净值、净值日期、Provider、抓取时刻与 freshness。历史按净值日期严格升序。Server 直接校验上游版本和代码，并使用独立缓存键，旧缓存不作为当前响应读取。

## 报价与筹码当前路由

`GET /api/v1/thesis-ledger/market/quote?symbol=600519.SH` 与 `GET /api/v1/thesis-ledger/market/chip?symbol=600519.SH` 使用 Data Bearer Token，响应均为 `version: 1`。报价继续保留实际 Provider、上游来源、来源时间、抓取时间及单位字段；筹码仅在来源提供完整分布时返回 buckets。Server 按当前 Schema 校验原始响应并隔离旧缓存。

`GET /api/v1/thesis-ledger/market/trading-observation?symbol=510300.SH&assetType=ETF` 使用同一 Data Token，按当前北京时间返回证券、资产类型、日期、观察时刻、`normal / suspended / unknown` 及来源分页摘要。它受总开关及对应市场／资产的实时行情功能开关控制，读取期间策略修订、开关、路由或来源资格变化拒绝发布；每次重新生成的 `appliedAt` 不参与控制一致性比较。不修改行情来源或路由。CN 股票读取东方财富完整当日停牌列表，SH ETF 读取上交所基金停复牌查询并复用既有分页、交易停牌区间解析；申赎暂停不解释为交易停牌。SZ ETF 复用已启用富途的当前快照，仅消费停牌与证券状态；经原隔离 worker 有界读取，来源关闭、缺凭据、配置变化及未知字段保持 `unknown`，不改变现有报价路由。失败、截断、未知区间继续保持 `unknown`。该当前观察不能回填历史，HK／US 状态继续由同次富途快照提供。

## 基金持仓与汇率当前路由

`GET /api/v1/thesis-ledger/market/fund-holdings?symbol=000001.OF` 和 `GET /api/v1/thesis-ledger/market/fx-rates?baseCurrency=CNY&currencies=CNY,HKD` 使用 Data Bearer Token，响应使用 `version: 1`。基金持仓继续携带报告期、披露日期、Provider 与披露证据；汇率保留每条币种、日期、来源、时效及可用性。Server 不补写基金持仓版本或代码，按当前 Schema 校验，并隔离旧持仓缓存。

## 当前 Data 能力与 Control 路由

`GET /api/v1/thesis-ledger/capabilities` 只声明 `dataContract: "dsa-data-v1"`、`serviceCapabilities.fundNav: true` 和多窗口协议。`fundNav` 表示净值端点已安装，不表示某只基金的真实 Provider 当前可用；实际来源须由请求响应核验。Server 健康检查读取此端点。

Provider registry 当前通过 `GET /api/v1/thesis-ledger/control/providers` 返回；配置、只读测试、移除分别使用 `/api/v1/thesis-ledger/control/providers/{providerId}/config`、`/test`、`/remove`。这些路由使用独立 Control Token 与 `contractVersion: 1`。凭证只接受结构化 `credentials`，旧 `credential` 单字符串及旧版本信封在修改状态前被拒绝。Longbridge OAuth 会话使用 `/api/v1/thesis-ledger/control/providers/longbridge/oauth/sessions`，创建请求使用当前 Control 信封；当前会话、按 ID 查询和取消仍使用同一路径前缀。

Registry、配置、测试、移除及 OAuth 成功响应均携带 `contractVersion: 1` 和 `consumer: "thesis-ledger"`。配置、测试与移除响应回传 `requestId` 和 `providerId`；Server 校验版本、consumer、请求身份和 Provider 身份后再消费，移除的 tombstone 必须属于同一 Provider。OAuth 查询和取消响应必须属于请求的会话；取消请求体使用当前 Control 信封，旧版本不会更改会话状态。Server 对桌面继续提供既有公开会话结构，不透传令牌。

```text
POST /api/v1/thesis-ledger/control/handshake
POST /api/v1/thesis-ledger/control/policies/apply
GET /api/v1/thesis-ledger/control/policies/effective
GET /api/v1/thesis-ledger/control/routes/capabilities?contractVersion=1
GET /api/v1/thesis-ledger/catalog/snapshot
GET /api/v1/thesis-ledger/catalog/delta?cursor=generation:1
POST /api/v1/thesis-ledger/control/catalog/jobs
GET /api/v1/thesis-ledger/control/catalog/jobs/{jobId}
POST /api/v1/thesis-ledger/control/catalog/ack
```

请求必须携带：

```text
Authorization: Bearer ${THESIS_LEDGER_DSA_TOKEN}
```

Control 路由必须使用独立的控制凭证，不能复用 Data Contract Token：

```text
Authorization: Bearer ${THESIS_LEDGER_CONTROL_TOKEN}
```

浏览器和客户端只访问 ThesisLedger facade；Control Token 只存在于 ThesisLedger 服务端到 DSA 的请求链路。

生效策略读取默认使用 dsa-control-v1；错误合同返回 `CONTROL_CONTRACT_UNSUPPORTED`。

## 当前 Control 策略与 Provider

- Control 与 Data 分别使用独立 Token；当前 Control 请求固定 `contractVersion: 1`、`consumer: "thesis-ledger"`，路由按 `RouteKey` 与 `providerId / upstreamSource` 精确匹配。
- Provider registry 声明各来源的真实能力。`REALTIME_QUOTE`、`DAILY_BAR`、`FUND_NAV`、`FUND_NAV_HISTORY` 和 `CHIP_SUMMARY` 只有在对应适配器、来源修订与准入证据有效时才成为可执行目标；有序目标上限为 2。
- Provider 凭证写入 DSA SQLite 的加密字段；响应只返回 `configured`、`credentialConfigured`、健康和能力状态，不回显凭证。空凭证保存表示保留旧值，显式 `clearCredentials` 才会清除。
- RQData 注册为 `rqdata`，通过既有 `/control/providers/rqdata/config` 保存 `credentials: { method: "username_password", values: { username, password } }`；首次保存要求两个字段完整，后续省略或空字段保留已有值，密码有效内容不去除首尾空格。单字符串 `credential` 拒绝。账号快照来自加密 Control 配置，不读取隐式环境账号；清除或删除 Provider 后快照无账号值。公共 registry 仅给出字段是否配置，配置成功不验证接口权限；当前可路由能力为空，不能据此请求行情或事件。账号修订绑定凭据来源与主密钥，读取前后轮换会拒绝晚到结果。
- Provider test 是只读、逐 Capability 的有界 smoke：fixture 模式调用确定性 fixture，非 fixture 模式调用对应 Provider 适配器，并写入 scoped health；只返回每项状态和稳定错误码，不返回原始异常或临时凭证。
- Policy Apply 要求单调递增 `revision`。同一 revision 的相同请求幂等，内容冲突或旧 revision 拒绝；DSA 先原子写入 Desired/Effective projection，再按当前配置和实际单标适配能力计算每条路由的 eligible Provider。Desired Policy/Provider manifest 记录用户期望能力；Effective Policy 必须反映当前可执行能力，不能把不安全的全市场函数伪装成单标能力。
- Provider fallback 只在同一 capability 的有序候选内发生。Quote/NAV 是 record-level，Bars/NAV history 是 sequence-level，`CHIP_SUMMARY` 是摘要级；响应保留实际 `provider` 和 `fallbackUsed`，不使用 `provider=CACHE`。Indicator 只能继承统一 gateway 的 `DAILY_BAR` 输入来源，不调用 DSA native `DataFetcherManager`。
- Catalog 通过完整快照或带 cursor 的 delta 传输，使用 `generation`、`checksum`、`cursor` 和持久化 ACK。ThesisLedger 只有校验完整快照，或原子应用 delta 并重算完整 checksum 后，才切换本地目录状态；cursor 过期时回退到完整快照。
- Catalog trigger 只创建或复用 `pending`/`running` Job 并快速返回；Provider 抓取由 DSA worker 异步执行。调用方通过受 Control Token 保护的 Job status 查询观察 `pending`、`running`、`succeeded`、`failed` 和 `timeout`，只有 `succeeded` 才允许 ACK 新 generation。
- Catalog Job 响应包含 `contractVersion: 1`、`consumer: thesis-ledger`；触发响应回传本次 `requestId`，轮询绑定路径中的 Job ID。ACK 必须携带非空请求身份、正整数 generation 和 SHA-256 checksum，响应回传同一信封、身份、checksum 与 cursor。ACK 的当前目录核验与写入共享 SQLite 写事务，迟到的旧 generation 返回 409。Snapshot/Delta 读取重算持久化目录摘要并验证条目唯一及游标身份；现行游标为 `generation:<正整数>`，`0`、`generation:0` 和未知游标返回 409，初次同步直接读取无 cursor 的完整快照。

## 运行时与持久化边界

- DSA 原生分析继续使用既有 Provider 配置和 fallback；ThesisLedger consumer 只使用 Control Contract 的 Effective Policy，不读取原生默认优先级。
- DSA 的 ThesisLedger Policy、ProviderConfig、健康、Catalog generation 与 Provider 请求资格存在独立 SQLite 文件中，生产路径由 `DATABASE_PATH` 指定，并挂载独立持久化卷。健康、熔断与请求资格使用当前精确来源身份；ETF `REALTIME_QUOTE` 资格键包含 `provider + capability + instrumentType + symbol + upstreamSource`，默认最短冷却 600 秒。资格在实际上游调用前原子登记，显式刷新不能绕过，进程重启后仍生效；旧来源别名键不参与读取。
- ThesisLedger PostgreSQL 保存 Desired Policy、目录 Instrument、Asset 关联和产品缓存；Desktop 的 `/market-data` 是完整配置入口，Mobile 不提供配置或写入入口。

## 能力边界

当前回测的 Calendar 与 Instrument Facts 通过 `GET /api/v1/thesis-ledger/backtest/calendar`、`GET /api/v1/thesis-ledger/backtest/instrument-facts` 读取，响应 `version: 1`，继续按请求窗口与 `dataAsOf` 校验来源覆盖和知识时间。公司行动由 事件接口供当前 Snapshot 冻结。独立业务路由统一位于 `/api/v1/thesis-ledger`。

`instrument-facts` 的真实来源读取接受成组的 `barAdjustment`、`barProviderId`、`barUpstreamSource`、`barRouteIndex` 查询参数；DSA 将其与请求的 `market`、`instrumentType` 组成精确日线 RouteKey 和 RouteTarget，经当前 dsa-data-v1 准入路由读取。参数只给出部分时返回 422；未提供精确路由时历史可交易性保持 `unavailable`，不会自行改用 `none` 口径或其他来源。已核验 HiThink ETF 成功响应中的缺 Bar 日按下述日级规则分类。

HiThink ETF 来源适配器的显式 `allow_missing_sessions=True` 内部解析入口已接入 `instrument-facts` 的可交易性读取：成功且分页已核实的响应同时返回有效 Bar、缺失日期和原响应字节摘要。默认价格调用继续要求完整 Bar 窗口。

CN 日线行情请求可显式设置 `tradabilityMode: "assume-untradable-no-bar"`。成功响应同时返回实际 Bar 与 `historicalTradabilityWindows`：单窗一条，多窗按原来源窗口排列，每条保持自己的原响应 SHA-256、真实采集时间、分页完成证明和完整预期交易日状态分区。父窗口的状态并集必须覆盖独立日历，重叠日期状态一致，已观测交易日与实际 Bar 一一对应；来源窗口间的价格交集兼容性仍须成立。普通行情请求维持严格完整 Bar 校验。异常、零量、来源失败或未确认分页不作为缺日成功。

`instrument-facts` 的 `identityOnly=true` 只返回静态身份、交易单位等事实，不再次读取行情；`fact.tradable=false`，无日级证据。Server 将该原始响应独立冻结，并以同次执行行情的来源窗口证据组成回测依赖。当前采集的缺日假设只用于固定来源回测，不能据此建立历史 PIT 可用性。

真实生产响应的 `historicalTradability` 字段使用日级证据合同，绑定已上市范围、独立日历/上市来源、精确行情路由和来源修订、响应 SHA-256 及真实采集时间。正成交量完整 Bar 日期为 `observed-traded`，缺 Bar 日期为 `assumed-untradable-no-bar`；来源失败、异常/零量 Bar、证据不完整或晚于 `dataAsOf` 时不可用。`coverage.complete` 表示预期交易日均有状态；`fact.tradable` 仅表示窗口内存在已观测交易日，不能作为全部日期可交易的证明。全部日期缺 Bar 的已核验成功响应仍可提供完整状态。

日线价格研究可在 `tradabilityMode="assume-untradable-no-bar"` 时使用 `dailyObservationContract="market-daily-tradability-window-v1"`。窗口证据以 `priceOnlyAllowed` 记录该配置。启用时，有效正 OHLC 且成交量未知记为 `observed-price-only`，正量记为 `observed-traded`。缺日、真实零量、负量、错误 OHLC 和 Yahoo 特定来源的零量规则沿用原业务约束。消费者同时核对合同名、请求配置和证据字段。

`observed-price-only`表示价格观察，不授予真实份额执行或历史PIT资格。身份响应及冻结事实中的真实`tradable`断言仍只依据已交易观察；Server仅在明确采用版本2的归一化价格研究中消费价格观察，量能指标和参与率仍依赖实际有效量。跨仓消费者、冻结和重放验收由ThesisLedger的[整改任务](../../../thesis-ledger/docs/archive/tasks/2026-11-20-backtest-practical-design.md)持有。

首个生产者是已准入 HiThink ETF 日线；其他未提供完整证据元数据的来源保持不可用。上市事实使用已有独立来源的 `knownAt`，本地日历保守使用本次采集时间作为 `availableAt`；真实采集时间只建立当前快照证据，不核发历史决策 PIT 资格。Server 的新字段核验、Snapshot 冻结和 Runner 日级消费由后续阶段承担。

- ETF 单标报价只能使用已确认的单标适配器：efinance 使用 `get_quote_snapshot(symbol)`；当前 AkShare ETF 全市场接口不属于可执行目标，单标失败保持 unavailable，不回退全市场。新增 ETF 来源必须先声明并验证单标适配器。
- Fund NAV 接受 `.OF` 场外基金代码，返回单位净值、净值日期、Provider 和 delayed/stale/unavailable freshness；先校验上游完整序列，再按当前请求选最新点或历史窗口，实际返回行须落在来源准入范围内。该净值只作为估值输入，不作为截图审核结果。
- Fund holdings 保留报告期与原始百分比转换后的权重，不归一放大；未知真实披露时间返回 `disclosureDate: null`，不得用 `fetchedAt` 推定公告可见时间。持仓行异常拒绝，合法响应也不自动证明披露完整性。
- Bars 当前路由只支持 `1d`；`1m` 返回 `unsupported_capability`。
- 指标支持 MA、MACD、RSI；ATR 返回 `unsupported_capability`。
- Chip 通过显式 `CHIP_SUMMARY` 当前策略路由，至少返回摘要字段；没有可靠完整分布时省略 `buckets` 和 `mainPeak`，不使用估算值填充。未由 manifest 声明的 Provider/InstrumentType 组合在 Policy apply 时原子拒绝。
- `THESIS_LEDGER_FIXTURE_MODE=true` 时使用固定 fixture，供 CI 和集成门禁显式开启；默认值为 `false`，生产必须保持关闭，避免 fixture 结果被误当成 Effective Policy/真实 Provider 验收。

## 错误

错误响应的 `detail` 包含 `contractVersion`、稳定 `code`、`message`、`requestId` 和 `diagnosticId`。Data Contract 常见 code 为 `unauthorized`、`service_misconfigured`、`unsupported_capability`、`upstream_unavailable` 和 `upstream_invalid_response`；Control Contract 另有 `INVALID_CONSUMER`、`UNKNOWN_PROVIDER`、`STALE_REVISION`、`NO_ELIGIBLE_PROVIDER`、`CATALOG_CURSOR_EXPIRED`、`catalog_provider_timeout`、`catalog_provider_unavailable` 和 `catalog_all_providers_unavailable`。
