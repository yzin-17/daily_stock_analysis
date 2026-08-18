# ThesisLedger Contract V1

DSA Fork 通过独立的兼容层向同级 ThesisLedger 主仓提供行情和量化能力。该兼容层不改变 DSA 原生 `/api/v1` 路由，也不依赖管理员 session。

## 路由

```text
GET /api/v1/thesis-ledger/capabilities
GET /api/v1/thesis-ledger/market/fund-nav?symbol=000001.OF
GET /api/v1/thesis-ledger/market/fund-nav/history?symbol=000001.OF&start=...&end=...&limit=365
GET /api/v1/thesis-ledger/market/quote?symbol=600519.SH
GET /api/v1/thesis-ledger/market/bars?symbol=600519.SH&timeframe=1d
GET /api/v1/thesis-ledger/market/indicators/{MA|MACD|RSI}?symbol=600519.SH&timeframe=1d
GET /api/v1/thesis-ledger/market/chip?symbol=600519.SH
POST /api/v1/thesis-ledger/control/handshake
GET /api/v1/thesis-ledger/control/providers
POST /api/v1/thesis-ledger/control/providers/{providerId}/config
POST /api/v1/thesis-ledger/control/providers/{providerId}/test
POST /api/v1/thesis-ledger/control/providers/{providerId}/remove
POST /api/v1/thesis-ledger/control/policies/apply
GET /api/v1/thesis-ledger/control/policies/effective
GET /api/v1/thesis-ledger/catalog/snapshot
GET /api/v1/thesis-ledger/catalog/delta?cursor=generation:1
POST /api/v1/thesis-ledger/control/catalog/jobs
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

## Control Contract V1

- Control Contract 与 Data Contract 独立版本化，当前 `contractVersion=1`，consumer 固定为 `thesis-ledger`。
- Provider registry 的 MVP 标识为 `akshare`、`efinance`，能力使用 `REALTIME_QUOTE`、`DAILY_BAR`、`FUND_NAV`、`FUND_NAV_HISTORY`，并按 `capability × instrumentType` 保存有序路由。
- Provider 凭证写入 DSA SQLite 的加密字段；响应只返回 `configured`、`credentialConfigured`、健康和能力状态，不回显凭证。空凭证保存表示保留旧值，显式 `clearCredentials` 才会清除。
- Provider test 是只读、逐 Capability 的有界 smoke：fixture 模式调用确定性 fixture，非 fixture 模式调用对应 Provider 适配器，并写入 scoped health；只返回每项状态和稳定错误码，不返回原始异常或临时凭证。
- Policy Apply 要求单调递增 `revision`。同一 revision 的相同请求幂等，内容冲突或旧 revision 拒绝；DSA 先原子写入 Desired/Effective projection，再按当前配置计算每条路由的 eligible Provider。
- Provider fallback 只在同一 capability 的有序候选内发生。Quote/NAV 是 record-level，Bars/NAV history 是 sequence-level；响应保留实际 `provider` 和 `fallbackUsed`，不使用 `provider=CACHE`。
- Catalog 通过完整快照或带 cursor 的 delta 传输，使用 `generation`、`checksum`、`cursor` 和持久化 ACK。ThesisLedger 只有校验完整快照，或原子应用 delta 并重算完整 checksum 后，才切换本地目录状态；cursor 过期时回退到完整快照。

## 运行时与持久化边界

- DSA 原生分析继续使用既有 Provider 配置和 fallback；ThesisLedger consumer 只使用 Control Contract 的 Effective Policy，不读取原生默认优先级。
- DSA 的 ThesisLedger Policy、ProviderConfig、健康与 Catalog generation 存在独立 SQLite 文件中，生产路径由 `DATABASE_PATH` 指定，并挂载独立持久化卷。
- ThesisLedger PostgreSQL 保存 Desired Policy、目录 Instrument、Asset 关联和产品缓存；Desktop 的 `/market-data` 是完整配置入口，Mobile 不提供配置或写入入口。

## 能力边界

- Fund NAV V1 接受 .OF 场外基金代码，返回单位净值、净值日期、provider 和 delayed/stale/unavailable freshness；该净值只作为估值输入，不作为截图审核结果。
- Bars V1 只支持 `1d`；`1m` 返回 `unsupported_capability`。
- 指标支持 MA、MACD、RSI；ATR 返回 `unsupported_capability`。
- Chip 至少返回摘要字段；没有可靠完整分布时省略 `buckets` 和 `mainPeak`，不使用估算值填充。
- `THESIS_LEDGER_FIXTURE_MODE=true` 时使用固定 fixture，供 CI 和集成门禁使用；生产必须关闭。

## 错误

错误响应的 `detail` 包含 `contractVersion`、稳定 `code`、`message`、`requestId` 和 `diagnosticId`。Data Contract 常见 code 为 `unauthorized`、`service_misconfigured`、`unsupported_capability`、`upstream_unavailable` 和 `upstream_invalid_response`；Control Contract 另有 `INVALID_CONSUMER`、`UNKNOWN_PROVIDER`、`STALE_REVISION`、`NO_ELIGIBLE_PROVIDER` 和 `CATALOG_CURSOR_EXPIRED`。
