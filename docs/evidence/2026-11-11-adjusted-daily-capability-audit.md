# 复权日线非对称能力核实证据

## 范围与结论

本轮核实腾讯股票后复权、BaoStock ETF 日线、HiThink ETF 后复权、Yahoo 后复权，并复核 Tushare ETF 因子；不将本地未接入等同于上游不支持。长期语义见[复权能力说明](../design/thesis-ledger-adjusted-daily-capabilities.md)，本轮执行已完成，见[归档任务](../archive/tasks/2026-11-11-adjusted-daily-capability-audit.md)。

| 组合 | 上游核实 | 本轮行为 |
| --- | --- | --- |
| 腾讯股票后复权 | 官方端点实读 `sh600519` 的 `hfqday`，固定窗口 28 行 | 移除只允许 ETF 的本地限制，增加股票精确绑定及资产身份检查 |
| BaoStock ETF 日线 | `query_stock_basic(sh.510300)` 返回唯一 `type=5`；日线前后复权各 28 行且返回 `adjustflag=2/1` | 增加 ETF 未复权、前复权、后复权绑定，核对静态类型、完整代码和响应口径 |
| HiThink ETF 后复权 | 当前官方基金端点固定前复权，无复权参数；股票历史端点对 `510300.SH/backward` 返回 `1002`、`Unknown thscode` | 保持前复权，不能用 `adjust=null` 冒充未复权或后复权；未证明其他同花顺产品不支持 |
| Yahoo 后复权 | 当前实际 SDK 的 `back_adjust` 只缩放 Open/High/Low，保留原 Close；受控样本可使 Close 超出 High | 不将名称相似的 SDK 参数注册为后复权；当前 Chart/SDK 没有已核实的本系统后复权价格合同 |
| Tushare ETF 因子 | 官方 `fund_adj` 存在；当前控制凭据对 `fund_adj`、`fund_daily` 各一次实读均返回权限拒绝 | 记录账号限制，不能写成上游不支持。未认证因子锚点／ETF 合成价格通路，不注册为当前可用来源 |

HiThink [官方接口定义](https://fuyao.aicubes.cn/docs/mcp/tools/get_fund_market_historical/)明确当前端点价格口径。Yahoo [官方 SDK 源码](https://github.com/ranaroussi/yfinance/blob/main/yfinance/utils.py)与目标安装版本 1.8.0 的实际实现一致，本轮无 Yahoo 网络读取。Tushare [基金因子文档](https://tushare.pro/document/2?doc_id=199)与[官方复权说明](https://tushare.pro/document/2?doc_id=146)分开核对；`pro_bar` 的 `FD` 分支没有 ETF 复权运算。官方当前基金因子文档门槛为 2000 积分，当前账号声明为 120 分；权限结论还由实际接口拒绝确认。

## 上游直接读取

固定窗口为 `2026-09-24` 至 `2026-11-10`。只读 Docker 诊断使用 `python -B`，BaoStock 使用当前实际 SDK 会话，腾讯使用原生生产读取函数；未改数据库或策略。

- 腾讯 `600519.SH/hfq`：28 行，逐年响应摘要 `528e6548885c063e4c206e419545b82f022b764c5902f6a2672fba9bd6b7a2b4`，传输修订 `2a30182add0178ddc25fa20ca10b1697c8aac4a31e1f68fce713a9f196d78b63`。
- BaoStock `510300.SH/qfq`：静态代码 `sh.510300`、类型 `5`；28 行，唯一返回口径 `2`，解析行摘要 `2cf6fcc3d225c7e8c615fe61c741d120fa35c915f02bdde77559b4ae2d059e80`。
- BaoStock `510300.SH/hfq`：28 行，唯一返回口径 `1`，解析行摘要 `5248693b75c6bb1f6bce66aec2c13f2262c5875d705c69f137c48891a6c68c62`。
- Tushare 先尝试环境凭据时发现未配置，仅在本地构造阶段失败，未发起网络；随后通过 `mode=ro` 控制库读取当前凭据快照，各端点实读一次，均为 `TushareApiError` 权限拒绝。没有输出密钥、原始异常或账号资料。

数据调用初步为腾讯 1 次、BaoStock 静态 1 次与日线 2 次、Tushare 2 次，共 6 次；生产完整路径验收另计。

另对 HiThink 股票历史端点进行了两次只读核实：一次按现有适配器参数带 `offset=0`，一次按公开最小参数移除该项；相同 ETF 后复权请求均 HTTP 200、业务码 `1002`、`data=null`。第二次公开业务原因是 `Unknown thscode: 510300.SH`，因此不能借股票历史端点为当前 ETF 补后复权。未重试固定前复权基金端点，也未将该结果推广为整个同花顺产品线不支持。

## 本地验证

新增测试覆盖真实来源工厂、SQLite Control/Runtime、沪深 ETF 三口径、腾讯沪深股票后复权、类型／完整代码／口径错配、响应迭代中途失败、断言会话释放，以及 Yahoo `back_adjust` 的语义反例。旧股票读取与版本回归保留。

定向 4 文件最初 119 项通过；最终相关 16 文件 **1208 项通过**，4 项既有 FastAPI／Pytest 警告。首次相关测试发现三个旧测试仍断言“腾讯股票后复权未适配”，已按真实新契约更新后通过。最终还覆盖腾讯完整代码与推断交易所不一致时连接前拒绝，以及未复权诊断保持严格上市／日历门禁。

相关测试命令：`.venv/bin/python -m pytest tests/test_adjusted_daily_capability_audit.py tests/test_tencent_native_daily.py tests/test_tencent_fetcher.py tests/test_native_exact_daily.py tests/test_cn_adjusted_research_sources.py tests/test_thesis_ledger_baostock_capability_path.py tests/test_thesis_ledger_baostock_daily_status.py tests/test_thesis_ledger_baostock_lifecycle.py tests/test_thesis_ledger_baostock_status_routes.py tests/test_thesis_ledger_capability_registry.py tests/test_thesis_ledger_market_v3_admission_runtime.py tests/test_all_provider_exact_checks.py tests/test_configured_market_routes.py tests/test_backtest_daily_source_probe.py tests/test_thesis_ledger_hithink_etf.py tests/test_yfinance_daily_window.py -q --tb=short`。

工程门禁：在本仓把 `.venv/bin` 加入 PATH 后，`bash scripts/ci_gate.sh syntax`、`bash scripts/ci_gate.sh deterministic` 均退出 0；运行时文件 477 个，数据测试文件 431 个、部分文件 1 个、退役文件 200 个、目标 433 个。最初未加入虚拟环境时 `python` 不在 PATH，已修正执行环境，没有改门禁。

## 目标运行态验收

从 `/Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra` 执行 `./scripts/sync-code.sh dsa`，兼容性预检通过、退出 0、DSA 健康。首次快更后自审补充腾讯完整交易所身份拒绝，定向及门禁再次通过后使用同一入口同步最终源码；没有变更依赖、Schema 或镜像。镜像仍为 `sha256:1ec1f963ebaaafb59bfa8b4fb21bba484fdde6081f172b5c508f959d68c18b4e`。这是容器可写层更新，不是镜像发布。

生产窗口通过受维护的 `scripts/local-runtime/probe-backtest-daily-sources.sh` 运行，每次只在临时控制库副本中选择目标，复用生产请求解析、执行、覆盖和响应投影。通用命令参数为 `--symbol <代码> --asset <STOCK|ETF> --adjustment <口径> --start 2026-09-24 --end 2026-11-10 --target <来源/上游>`。未复权诊断新增 `none` 选项，仅用于已知上市及严格覆盖验证，不恢复普通回测表单选项。

| 来源 | 标的 | 口径 | 完整窗口 | 最终输入摘要 |
| --- | --- | --- | --- | --- |
| 腾讯 | `600519.SH` | 后复权 | 28／28 | `1c14401fd89bf4c759b71e129c7d7feb96db5d3c2c8394a7837f859a3bf5b041` |
| 腾讯 | `000001.SZ` | 后复权 | 28／28 | `baa3bb0145f7d1dac72fbaa89677ae65eea13500d49509e8127ffbf6ffbab65c` |
| BaoStock | `510300.SH` | 前复权 | 28／28 | `06763243be4ff9501443410a7bcfc4b372eadfbda52608efc7999ab33cede7d9` |
| BaoStock | `510300.SH` | 后复权 | 28／28 | `2aab4046eac7e8c8d04ca2a52686d8bc02c9e4f341db5e1fdcc38f010978a633` |
| BaoStock | `159919.SZ` | 前复权 | 28／28 | `d76e021f028f98fabc12becd9dee7c6ac7ed5f4db8b5e9a22d5c8f49e3da5be8` |
| BaoStock | `510300.SH` | 未复权 | 28／28 | `47793b2b09f5acdecb9634c876e3ac10a7cac6b8c61c375302ee36fb86a7ef02` |

全部窗口无缺日假设，外层完整读取 `pagesFetched=1`，均返回 `productionControlUnchanged=true`。腾讯两个窗口在最终身份校验代码同步后各复核一次；BaoStock 生产文件未再变更，复用首次通过结果。上市日期未知的股票和 `159919.SZ` 仍保持未知，未写入上市事实。量额单位仍为未知，不将这些价格读取认证为原始份额事件记账能力。

最终目录 `integrity=complete`，沪深股票／ETF 日线 48 个绑定，新增的四个绑定均 `ready`。真实控制策略 SQL 只读复核为修订 197，未创建或重跑回测任务。三份生产文件宿主／容器 SHA-256 一致：

- `data_provider/tencent_fetcher.py`：`751ead4a29596cf03d3c61c8442fbbd581d2cfb2c212d5885a10599c2d0bc39f`。
- `data_provider/thesis_ledger_sources/tencent.py`：`3833e59f295e21d6fc347de1a993fec676c7a15345a0913b6dfb6a617d4181c5`。
- `data_provider/thesis_ledger_sources/baostock.py`：`accad9b9c7ea655830ca89a4de149e202a88e4bad16dccfd5665df46c3e219f1`。

最终预算核对：逻辑诊断 17 次（含 Tushare 环境凭据缺失的两次本地拒绝），实际公开数据／静态身份调用 20 次：腾讯 5、BaoStock 11、Tushare 2、HiThink 2、Yahoo 0；不含 SDK 登录／登出。均在规格预算内，不继续重复相同输入。两仓 `git diff --check` 通过；未提交、推送或发布。
