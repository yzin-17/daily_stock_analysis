# 沪深 ETF 来源核对与富途日线证据

后续腾讯股票后复权与 BaoStock ETF 三口径已补齐，HiThink／Yahoo 语义及 Tushare 当前账号权限已继续核实，见[后续能力证据](2026-11-11-adjusted-daily-capability-audit.md)。下文保留本轮当时的目录范围与计数。

本轮[规格](../archive/specs/2026-11-11-futu-cn-etf-daily.md)、[任务](../archive/tasks/2026-11-11-futu-cn-etf-daily.md)已完成归档，长期接口说明见[富途接入](../design/thesis-ledger-futu-openapi.md)。

## 富途缺口与依据

富途[官方行情说明](https://openapi.futunn.com/futu-api-doc/qa/quote.html)的沪深证券范围包含 ETF；[历史 K 线接口](https://openapi.futunn.com/futu-api-doc/quote/request-history-kline.html)支持显式复权参数与分页，[账号权限和历史额度](https://openapi.futunn.com/futu-api-doc/intro/authority.html)须独立满足。

原代码已注册港美股票／ETF 12 个日线绑定及沪深股票 3 个，合计 15 个；沪深 ETF 缺失，且 `read_futu_history` 在连接前主动将所有内地日线限制为股票。当前问题是本地接入范围，不能据此解释为富途上游没有 ETF。

本轮新增沪深 ETF 的未复权／前复权／后复权 3 个绑定，Futu 日线共 18 个。复用原工厂、凭据、隔离 Worker、显式 `AuType`、64 页上限及异常分类。沪深 ETF 使用共享 CN 资产身份校验，仍须由 `get_stock_basicinfo` 返回唯一完整代码且 `stock_type=ETF` 后才请求历史 K 线。旧股票市场前缀、港美范围及证券类型拒绝保持。

前后复权研究使用既有独立日历；本地上市日期未知时保持 `status=unknown`。行情未复权读取不授予原始份额或公司行动记账资格，本轮未修改普通表单价格选项。

## 其他主要来源的当前范围

本轮只重读当前目录与代码，不重复上轮已成功的外部读取。起始沪深股票／ETF 日线共 41 个绑定，修复后 44 个；计数包括未复权，不表示每项均已真实验证。

| 来源 | 当前沪深 ETF 日线口径 | 当前结论与限制 |
| --- | --- | --- |
| 富途 | 未复权、前复权、后复权 | 本轮补齐；沪深前复权与沪市后复权实际窗口通过 |
| 腾讯 | 未复权、前复权、后复权 | 已接；前复权复用既有 `510300.SH` 完整窗口证据，后复权本轮不重读 |
| AkShare 东方财富 | 未复权、前复权、后复权 | 已接；前复权完整窗口沿用既有成功证据，不将一次网络失败解释为永久不可用 |
| EFinance 东方财富 | 未复权、前复权、后复权 | 已接；前复权沿用既有完整窗口证据 |
| HiThink | 前复权 | 已接；原基金历史接口仅声明该精确价格口径，沿用已有窗口证据 |
| Yahoo | 前复权 | 已接；多日窗口末日缺价的单日补取修复已有股票／ETF 28 会话证据 |
| Tushare | 未复权 | `fund_daily` 已接 ETF；独立 `fund_adj` 代码只输出因子观测，明确 `anchor=None`、`conversionAvailable=False`，未组成通过契约验证的前后复权价格来源 |
| BaoStock | 无 ETF 日线绑定 | 当前仅接股票日线；本轮未证明或登记 ETF 读取，不把本地未适配写成上游不支持 |
| Pytdx | 无 ETF 日线绑定 | 当前只接股票未复权；没有 ETF 复权回测通路 |
| TickFlow | 无 ETF 日线绑定 | 当前已配置但关闭，目录只有股票日线且 `not_admitted`；本轮不启用或调用 |
| RQData | 无当前可用 ETF 日线通路 | 当前关闭、未配置；不调用、不假定账号可用 |

既有证据：[来源基线](../../../thesis-ledger/docs/evidence/2026-11-11-backtest-source-compatibility.md)、[Yahoo 后续修复](2026-11-11-yahoo-daily-price-missing.md)。Tushare 官方[通用行情说明](https://tushare.pro/document/2?doc_id=109)也将其 `adj` 参数限定为股票，不能简单把 ETF 请求传入 `qfq/hfq` 就当作已实现基金复权。

## 当前账号的真实窗口

在 DSA 仓执行受维护的 `scripts/local-runtime/probe-backtest-daily-sources.sh`，指定 `--asset ETF --target futu/futu --start 2026-09-24 --end 2026-11-10`；后复权另传 `--adjustment hfq`。真实控制库只读，临时副本中选择精确目标，复用生产完整校验；没有保存真实主备策略。

| 标的 | 口径 | 日线／预期会话 | 缺日假设 | 耗时 | 结果 |
| --- | --- | --- | --- | --- | --- |
| `510300.SH` | 前复权 | 28／28 | 空 | 770 ms | 通过 |
| `159919.SZ` | 前复权 | 28／28 | 空 | 679 ms | 通过 |
| `510300.SH` | 后复权 | 28／28 | 空 | 678 ms | 通过 |

输入指纹依次为：

- `232b5423eeb98d4f076d74776a8d4a998805ee06ed8db2988ab115143f5d07a0`
- `c57203b0616078ed8929aab37e3ebdeb6fbaa7f9261b4edb8aec9d2318bcf1de`
- `a40ae4d105af6ad5ec73510c5d0938e691280f216287fc68371638708f936b30`

`510300.SH` 使用已有真实上市事实；`159919.SZ` 的本地上市日期仍为未知，没有新增或推断上市日期。三个窗口均 `productionControlUnchanged=true`。

本轮只有 3 次逻辑实源窗口，每次为一次完整隔离执行。`pagesFetched=1` 代表原合同的完整隔离响应；SDK 内部分页不导出，未从行数推算分页数。静态资料与历史 K 线均按原执行面读取，不更改账号行情权限或历史额度。

## 验证与部署

- 定向与相邻回归：13 个测试文件、1170 项通过。包含新增沪深 ETF 三口径的真实工厂／Worker／受控 SDK 双页闭环、错误代码／资产／静态资料、后页失败、权限、限频、额度、超时与上下文关闭；既有股票、港美、分钟和研究日历保持。
- `scripts/ci_gate.sh syntax` 与 `deterministic` 通过，能力边界、运行时打包输入及测试登记通过，空白检查通过。
- 在 `thesis-ledger-infra` 执行 `./scripts/sync-code.sh dsa`，兼容预检通过，退出 0，DSA 恢复健康；随后完成上述独立真实窗口读取。
- 独立核对两份本轮生产文件的宿主／目标 SHA-256，全部一致。实际目录 `complete`，新三个 ETF 绑定均 `ready`。
- 原真实策略修订仍为 `197`，旧任务 `ed08b1ad-82b3-4769-82f7-095a1925b5b3` 仍为 `failed`；未新建业务回测、未改写历史或调用 AI。
- 本轮仅代码快更，镜像保持 `sha256:1ec1f963ebaaafb59bfa8b4fb21bba484fdde6081f172b5c508f959d68c18b4e`，未构建／发布新镜像。未提交或推送，其他工作区改动保留。

## 范围与恢复

真实成功仅覆盖表中标的、日期和口径，不认证所有 ETF、其他市场、所有后复权或原始份额回测。各来源的原始行情、复权、权益事件及单位资格继续分开；未配置或关闭来源保持原状态。

如需撤回，仅恢复本轮 Futu 绑定与 ETF 历史读取条件，再通过 Infra 正式入口同步，保留同文件其他任务改动；无需恢复凭证、主备或业务数据库。
