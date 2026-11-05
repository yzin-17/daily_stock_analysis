# ThesisLedger V3 精确净值生产

## 入口与范围

`POST /api/v3/thesis-ledger/backtest/nav-inputs` 使用 `THESIS_LEDGER_DSA_TOKEN` 鉴权。首个适配器固定为 `CN / MUTUAL_FUND / FUND_NAV_HISTORY / efinance / eastmoney`；请求须给出准确 `routeIndex`、Desired/Effective/Catalog 版本、基金类型、区间、预热和处理尾部预算。

精确历史净值适配器与来源修订分别为 `efinance-fund-nav-raw-v1`、`eastmoney-fund-nav-raw-v1`。旧版本准入不能授权新接口；审核和部署由独立阶段实施。

## 研究可见性

调用方显式提交 `research-assumption` 与 `calendarDecisionRaw`。普通基金还须提交 `domesticRuleDecisionRaw`，使用 T+1 日终可见研究假设；QDII 仅接受已核查的基金级规则，110011 为 T+1，118001 为 T+2。`strict-publication` 返回 `publication_unavailable`，研究时间不能作为来源真实发布时间。

基金类型取东方财富当前基金列表的代码和类型字段，未知、缺失或重复记录拒绝。此身份检查不证明历史类型转换、暂停或限购完整性；研究范围及基金规则应由调用方评审。

估值日取来源净值日期；处理日取与已核验 XSHG 交易日的交集；披露工作日独立取 XSHG。缺日不交易，预热、确认结算尾部或披露可见性不足拒绝。真实暂停、限购、投资者及渠道差异不模拟，结果消费方必须披露。

研究日历搜索上限限制在实际来源采集日内；固定展望预算不要求读取尚未发生或无关的跨年日历。最终仍逐项核验预热、处理尾部和披露工作日，范围缩小后不足时继续拒绝。普通基金研究规则的适用范围必须明确包含实际预热日期，不能只声明运行区间。

## 证据与守卫

读取前后核验策略、目录与精确准入，后置范围包含实际预热和尾部。读取中停用、换版本、撤销、凭据安全条件变化，均拒绝晚到结果。

响应保留事实、日期、可见性、准入投影、来源修订，以及 `responseRaw`、`publicationRecords`、`ruleRaw`、`calendarRaw`、`assumptionRaw`、`calendarDecisionRaw`。来源信封另保存原生分页、原生逐条净值、基金身份原文及规则文件字节；上游十进制字符串不转成浮点数。

`publicationRecords` 是带 `dsa-eastmoney-nav-record-v1` 修订的可核验投影，原生记录另存于 `nativeRecordRaw`，稳定记录 ID 绑定原生摘要。冻结消费须重新核验原文摘要、来源页、规则文件、假设及日期的关联，不能只信任 `coverage.complete`。

## 验收层次

受控测试验证错误与竞态，隔离 SQLite 和鉴权 HTTP 验证接口，真实来源数据再经过当前 Schema 与 Server 的 Parquet 冻结及离线读回。目标部署、Server 准备和业务 Run 执行由后续任务验收。
