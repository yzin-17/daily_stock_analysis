# ThesisLedger 展示日历合同

`GET /api/v3/thesis-ledger/market/calendar` 使用现行 DSA Bearer Token，接受 `market / start / end / dataAsOf`。首批只支持 CN；日期与带时区评估时刻必须有效，开始日期不晚于评估日期，总跨度不超过 20000 个自然日，未来计划不超过 366 日。超限或非法输入返回 422/`invalid_request`，错误市场返回 `unsupported_capability`。

端点复用现行版本 3 日历事实信封、已验证可用时刻及 exchange-calendars 实际覆盖。只有包及登记可用性、请求范围同时满足才返回 supported；来源不可用或范围超出实际覆盖返回 unavailable、complete=false 和空 facts。计划读取不采集未来行情，不证明标的交易场所、严格 PIT 或派生能力准入。

展示周期需要整个年度的计划 Session，因此其未来预算与 `/backtest/calendar` 的结算预算独立；回测路径继续限制未来 104 日，两入口复用事实读取与响应装配，但分别验证请求范围。Data V3 握手、Provider 配置、行情日期限制与数据库结构没有变化。

验证使用 `tests/test_thesis_ledger_market_calendar.py`，覆盖年初完整年度、366 日与总范围边界、错误降级、认证及原回测预算。目标验收应记录实际包版本、可用时刻、范围与 Server 投影，不将受控 fixture 当作真实来源证据。

## 固定来源股票研究的行情覆盖日历

`POST /api/v3/thesis-ledger/market/bars` 的 CN STOCK 腾讯 `none/qfq` 请求显式携带 `tradabilityMode=assume-untradable-no-bar` 时，原有限官方日历范围不足可以复用已核验的现行 `calendar_fact`。这复用来源而不扩大原字典范围；旧窗口仍使用原证明，其他来源、资产、复权口径及未选择该研究假设的请求保持原边界。

新增接缝由 `src/services/thesis_ledger_research_calendar.py` 拥有。现行来源包、范围、时区及可用时刻必须通过核验；coverage 的来源为 `exchange-calendars/XSHG`，revision 保留包版本并追加 `availableAt`，不将其解释为研究期间的历史首次可见。周末及来源声明的休市日不生成会话，异常或尚未可用的来源继续不可用。

上市事实、分页、证券身份、价格及缺日校验保持。缺 Bar 仅按已有假设说明，不自动证明停牌；未知上市事实仍被当前合同限制，后续研究合同另行接入。新增定向测试为 `tests/test_thesis_ledger_research_calendar.py`，目标部署与扩展回测验收由主仓 C3 维护。
