# 股票与 ETF 复权日线的适配边界

## 能力判定

来源能力按市场、资产类别、周期、复权方式及上游端点共同登记。目录缺少某个组合仅说明当前本地适配缺失，不能据此认定上游不支持。

对外说明必须区分：上游明确支持且本地已接入、上游支持但本地未接入、当前账号权限拒绝、上游明确限定、尚未证实。目录 `ready` 也不能替代具体标的完整日期窗口验证。账号启用、凭据和路由选择分别判断。

## 当前非对称项

截至 2026-11-11，腾讯股票后复权及 BaoStock ETF 三种日线口径已补齐：

| 来源 | 股票日线口径 | ETF 日线口径 | 依据与接入边界 |
| --- | --- | --- | --- |
| 腾讯 | 未复权、前复权、后复权 | 未复权、前复权、后复权 | 显式请求 `newfqkline/get` 的对应口径，后复权只接受 `hfqday`，拒绝用 `day/qfqday` 顶替；按年分段并核对传输摘要 |
| BaoStock | 未复权、前复权、后复权 | 未复权、前复权、后复权 | ETF 在同一 SDK 会话中先以 `query_stock_basic` 确认唯一完整代码且 `type=5`，再请求日线；返回 `adjustflag` 必须与请求完全一致 |
| HiThink 当前基金端点 | 股票另走独立行情接口 | 前复权 | 当前基金历史端点固定前复权；当前股票历史端点对 ETF 后复权请求返回 `1002/Unknown thscode`。其他产品／账号不能凭此直接推定 |
| Yahoo 当前在线端点 | 前复权 | 前复权 | 统一使用 SDK `auto_adjust`；`back_adjust` 保留未复权 Close 而缩放 Open/High/Low，不是本系统的后复权 OHLC 合同 |
| Tushare 当前精确日线 | 未复权 | 未复权 | `fund_adj` 是真实存在的基金因子接口，`pro_bar(asset=FD)` 没有执行 ETF 复权；当前账号基金行情和因子读取均被权限拒绝，未认证合成复权行情 |

新增组合沿用独立研究日历和上市日期未知语义，不推断上市日期，不改变历史价格、实际份额记账或普通表单的选项。BaoStock 原股票绑定及版本保持；ETF 新绑定有独立版本。腾讯原有 ETF 后复权版本保持，股票新增独立版本。

## 港美股股票研究

富途港美股股票的未复权、前复权与后复权日线均接入研究日历及来源身份策略。不复权仅在显式研究模式下作为原价参考，普通严格请求仍要求原覆盖事实。身份来自已有复合观察，仅覆盖被核验的证券、市场及可见截点；不因为价格读取成功而推断其他证券、历史整手、价位、上市日期或原始份额资格。未知身份与市场不匹配继续拒绝。

使用 `scripts/local-runtime/probe-backtest-daily-sources.sh --adjustment none --price-research-reference` 可按实际研究参考请求诊断；不提供参考开关时保持严格模式。诊断默认窗口上限为 93 天，长窗须显式提供 `--max-window-days 400` 或 `550`，请求次数与超时预算不变。读取合同见[主仓规格](../../../thesis-ledger/docs/specs/2026-11-12-futu-raw-research-reference.md)，本次完整窗口结果见[验收记录](../../../thesis-ledger/docs/archive/evidence/2026-11-12-futu-raw-research-reference.md)。

精确日线目录的 `ready` 表示已接入该来源能力，研究窗口还须通过身份、日历和完整性校验。2026-11-11 的苹果与腾讯后复权修复及真实窗口结果见 [ThesisLedger 证据](../../../thesis-ledger/docs/evidence/2026-11-11-overseas-hfq-backtest.md)。

## 验证与来源

- [本轮上游核实、账号结果及目标窗口证据](../evidence/2026-11-11-adjusted-daily-capability-audit.md)。
- [HiThink 当前 ETF 历史接口](https://fuyao.aicubes.cn/docs/mcp/tools/get_fund_market_historical/)。
- [Yahoo SDK 复权实现](https://github.com/ranaroussi/yfinance/blob/main/yfinance/utils.py)。
- [Tushare 基金因子](https://tushare.pro/document/2?doc_id=199)、[复权行情说明](https://tushare.pro/document/2?doc_id=146)、[官方 SDK](https://github.com/waditu/tushare/blob/master/tushare/pro/data_pro.py)。

本轮未实现基于任意窗口首行重新锚定的 Yahoo 后复权，也未将未经锚点和价格语义验证的 Tushare 因子直接转为价格来源。
