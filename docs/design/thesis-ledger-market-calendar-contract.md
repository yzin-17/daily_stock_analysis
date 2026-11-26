# ThesisLedger 展示日历合同

`GET /api/v1/thesis-ledger/market/calendar` 使用现行 DSA Bearer Token，接受 `market / start / end / dataAsOf`。首批只支持 CN；日期与带时区评估时刻必须有效，开始日期不晚于评估日期，总跨度不超过 20000 个自然日，未来计划不超过 366 日。超限或非法输入返回 422/`invalid_request`，错误市场返回 `unsupported_capability`。

端点复用现行版本 3 日历事实信封、已验证可用时刻及 exchange-calendars 实际覆盖。只有包及登记可用性、请求范围同时满足才返回 supported；来源不可用或范围超出实际覆盖返回 unavailable、complete=false 和空 facts。计划读取不采集未来行情，不证明标的交易场所、严格 PIT 或派生能力准入。

展示周期需要整个年度的计划 Session，因此其未来预算与 `/backtest/calendar` 的结算预算独立；回测路径继续限制未来 104 日，两入口复用事实读取与响应装配，但分别验证请求范围。`dsa-data-v1` 握手、Provider 配置、行情日期限制与数据库结构没有变化。

验证使用 `tests/test_thesis_ledger_market_calendar.py`，覆盖年初完整年度、366 日与总范围边界、错误降级、认证及原回测预算。目标验收应记录实际包版本、可用时刻、范围与 Server 投影，不将受控 fixture 当作真实来源证据。

## 固定来源研究的行情覆盖日历

`POST /api/v1/thesis-ledger/market/bars` 显式携带 `tradabilityMode=assume-untradable-no-bar`，且精确 `DailyBinding.research_calendar_eligible` 校验通过时，有限官方日历范围不足可以复用已核验的现行 `calendar_fact`。精确绑定拥有支持范围，公共日历模块不按提供方名字授予资格。旧窗口优先使用原证明，普通严格请求继续要求已知上市事实及完整会话数据。

新增接缝由 `src/services/thesis_ledger_research_calendar.py` 拥有。现行来源包、范围、时区及可用时刻必须通过核验；coverage 的来源为 `exchange-calendars/XSHG`，revision 保留包版本并追加 `availableAt`，不将其解释为研究期间的历史首次可见。周末及来源声明的休市日不生成会话，异常或尚未可用的来源继续不可用。

CN 身份由依赖事实模块的 `cn_research_calendar_eligible` 统一校验，股票与 ETF 不混用。EFinance、AkShare、BaoStock、富途已注册的 CN 不复权及前／后复权日线，以及 HiThink、腾讯相应已支持绑定，按该语义接入；BaoStock 仍限沪深，Yahoo 仍仅有其已注册的前复权，未注册组合继续拒绝。港美来源保持各自身份范围。不复权参考用于策略收益研究时复用显式 `tradabilityMode`，允许上市信息保持未知；严格请求、精确证券身份、日历、完整分页和行情有效性检查保持原合同。该声明不产生真实交易资格或权益现金流证明。消费侧规格见相邻 Thesis Ledger 的 `docs/specs/2026-11-11-backtest-return-research.md`。

明确研究请求可以保留 `listing.status=unknown`，不补造上市日期。已知上市事实仍按其真实日期裁剪，多窗口调度统一消费覆盖上下文的 `expectedPostListingSessionDates`，不直接索引可能未知的 `firstTradingDate`。缺 Bar 仅生成明确的 `assumed-untradable-no-bar` 假设，不证明停牌；分页、证券身份、OHLC、来源关联、单位和真实日历检查继续执行。单位未知不会因此取得原始份额或公司行动记账资格。

回归见 `tests/test_thesis_ledger_research_calendar.py`、`tests/test_cn_adjusted_research_sources.py` 和 `tests/test_thesis_ledger_multi_window_api.py`。实际来源结果与限制见[完整窗口检查](../../../thesis-ledger/docs/evidence/2026-11-11-backtest-source-compatibility.md)。

## 完整来源窗口诊断

在 DSA 仓执行以下维护入口，针对唯一运行中的 `thesis-ledger-dev` 的 DSA 容器诊断。它只将当前脚本通过标准输入执行，不安装代码或更新目标服务；目标源码更新仍使用 Infra 的 `sync-code.sh` 或 `update.sh`。

```bash
bash scripts/local-runtime/probe-backtest-daily-sources.sh \
  --symbol 600519.SH --asset STOCK --adjustment qfq \
  --start 2026-09-24 --end 2026-11-10 \
  --target efinance/eastmoney --target baostock/baostock
```

读取真实控制库的只读快照，在临时副本中设置精确目标，复用生产完整窗口读取及响应校验；临时目录与副本权限分别为 `0700`、`0600`，退出时清理。输出包含实际尝试、失败阶段、脱敏异常位置、缺日假设及指纹；不输出凭证或供应商异常原文。前后控制状态变化时拒绝认证。本入口不修改真实主备策略、不签发准入、不创建业务回测；默认研究模式，窗口最多 93 天、最多 12 个去重目标、每提供方最多 4 个目标，跨调用总预算由执行任务记录，SDK 内部预算保持。
