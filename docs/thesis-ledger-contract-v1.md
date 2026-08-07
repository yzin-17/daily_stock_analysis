# ThesisLedger Contract V1

DSA Fork 通过独立的兼容层向同级 ThesisLedger 主仓提供行情和量化能力。该兼容层不改变 DSA 原生 `/api/v1` 路由，也不依赖管理员 session。

## 路由

```text
GET /api/v1/thesis-ledger/capabilities
GET /api/v1/thesis-ledger/market/fund-nav?symbol=000001.OF
GET /api/v1/thesis-ledger/market/quote?symbol=600519.SH
GET /api/v1/thesis-ledger/market/bars?symbol=600519.SH&timeframe=1d
GET /api/v1/thesis-ledger/market/indicators/{MA|MACD|RSI}?symbol=600519.SH&timeframe=1d
GET /api/v1/thesis-ledger/market/chip?symbol=600519.SH
```

请求必须携带：

```text
Authorization: Bearer ${THESIS_LEDGER_DSA_TOKEN}
```

## 能力边界

- Fund NAV V1 接受 .OF 场外基金代码，返回单位净值、净值日期、provider 和 delayed/stale/unavailable freshness；该净值只作为估值输入，不作为截图审核结果。
- Bars V1 只支持 `1d`；`1m` 返回 `unsupported_capability`。
- 指标支持 MA、MACD、RSI；ATR 返回 `unsupported_capability`。
- Chip 至少返回摘要字段；没有可靠完整分布时省略 `buckets` 和 `mainPeak`，不使用估算值填充。
- `THESIS_LEDGER_FIXTURE_MODE=true` 时使用固定 fixture，供 CI 和集成门禁使用；生产必须关闭。

## 错误

错误响应的 `detail` 包含 `contractVersion`、稳定 `code` 和 `message`。常见 code 为 `unauthorized`、`service_misconfigured`、`unsupported_capability`、`upstream_unavailable` 和 `upstream_invalid_response`。
