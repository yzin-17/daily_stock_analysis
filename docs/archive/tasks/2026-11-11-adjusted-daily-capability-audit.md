# 复权日线能力核实任务

规格：[复权日线能力核实与接入](../specs/2026-11-11-adjusted-daily-capability-audit.md)。

## 当前状态

本轮五项核实已收敛：补齐腾讯股票后复权和 BaoStock ETF 三口径日线，六组完整真实窗口全部 28／28，无缺日假设。HiThink 当前基金端点固定前复权，股票端点拒绝 ETF；Yahoo 参数语义不同；Tushare 当前账号基金行情／因子实读被权限拒绝，未认证合成复权价格。1208 项相关测试、工程门禁、正式快更及三份源码匹配均通过。目录新增四项均 ready，真实策略仍为修订 197。详细记录见[证据](../../evidence/2026-11-11-adjusted-daily-capability-audit.md)。

## 执行项

- [x] A1：腾讯股票后复权。输入 `tencent_native_daily.py`、来源绑定与腾讯官方端点；已补齐资产绑定、工厂路径与错误资产／交易所拒绝测试，对应 AC-01 至 AC-03。
- [x] A2：BaoStock ETF 日线。静态 ETF 身份、三口径返回标签、沪深工厂路径及错误拒绝均通过，对应 AC-01 至 AC-03。
- [x] A3：HiThink ETF 后复权。已核对官方基金接口并实际检查股票端点；当前没有可认证后复权通路，未扩大到其他同花顺产品，对应 AC-01 至 AC-03。
- [x] A4：Yahoo 后复权及 Tushare ETF 因子核实。已用实际 SDK 和受控语义反例拒绝误映射，Tushare 真实账号明确权限拒绝；不声称因子不存在或整体上游不支持，对应 AC-01 至 AC-03。
- [x] A5：相关门禁、目标同步与真实生产路径验证。六组窗口、控制状态及源码匹配通过，对应 AC-04。
- [x] A6：证据、长期说明与最终一致性核对。对应 AC-05，本轮核实与可证实适配范围已收敛。

## 上下文入口

- `data_provider/thesis_ledger_sources/{tencent,baostock,hithink,yfinance,tushare}.py`
- `data_provider/tencent_native_daily.py`、`data_provider/hithink_etf_capability.py`
- `src/services/thesis_ledger_hithink_etf.py`、`data_provider/tushare_fund_adj.py`
- `scripts/probe_backtest_daily_sources.py`、`scripts/local-runtime/probe-backtest-daily-sources.sh`
- 既有证据：`docs/evidence/2026-11-11-futu-cn-etf-daily.md`、`docs/evidence/2026-11-11-yahoo-daily-price-missing.md`

## 计划预检

各执行项按单一来源接缝拆分，可独立测试；真实运行态验收单独维护。用户已授权能力核实及适配，不包含真实策略变更或历史任务重跑。未确认的上游定义阻止对应绑定注册，不阻塞其他来源核实。

## 最终一致性核对

AC-01 至 AC-05 均有实际输入、实现和分层证据；价格来源、因子接口、账号权限与整家供应商能力没有混同。长期合同由[复权能力说明](../../design/thesis-ledger-adjusted-daily-capabilities.md)维护。Tushare 因子权限开放及 Yahoo 后复权定义属于未满足的外部条件，不冒充已接入，也不写成上游不支持；本轮没有已确认而未实施的直接后复权价格接口。历史表格保留原基线，并补最新证据入口。未构建镜像、未提交或推送。
