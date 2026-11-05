# ThesisLedger 派生周期指标合同

## 范围

DSA 对主仓提供的周期点进行纯指标计算，支持 `5d / 1w / 1mo / 1y`。主仓负责日线聚合、完整日历/Session、来源价格口径、评估时刻和覆盖证明；DSA 不采集基础行情或重建这些事实。本能力不改变 Provider 原始 bars 周期或 Data V3 capabilities 握手。

原生 `1m / 1d` 指标请求保留 Contract V3、原 identity/points 指纹与既有公式。四种新周期使用独立派生绑定；不能仅替换 timeframe 并将数值摘要当成完整聚合摘要。

## 请求绑定

计算路径仍为 `POST /api/v3/thesis-ledger/market/indicators/calculate`，使用现行独立 DSA Bearer Token。派生请求保留 `contractVersion / identity / points / requests / inputFingerprint`，并增加严格 `periodContext`：

| 字段                   | 含义                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------- |
| protocol               | 固定 `market-period-indicator-v1`                                                                     |
| aggregationFingerprint | 主仓完整聚合编码的 SHA-256，含规则、日历 revision、基础来源、评估时刻、覆盖、锚点、状态与 availableAt |
| pointFingerprint       | 复用现行规范化 identity/points 编码，对实际计算窗口点生成 SHA-256                                     |
| inputFingerprint       | 紧凑 JSON `["market-period-indicator-v1", aggregationFingerprint, pointFingerprint]` 的 SHA-256       |

DSA 校验实际点摘要、完整绑定摘要、证券类型、时间顺序、OHLCV/金额及完成状态。错误返回现行 `invalid_request`/422，不读取来源或用日线替代。调用方仍对聚合摘要的事实来源负责；DSA 验证摘要绑定，不提供交易日历准入证明。

新周期只接受 STOCK/ETF，计算点数为 1～3650，参数整数范围 2～200。参数语义和 MA/MACD/RSI 公式沿用现有实现。基金或未知周期不适配派生输入。

## 独立能力声明

`GET /api/v3/thesis-ledger/market/indicators/capabilities` 使用同一 DSA Bearer Token，声明 `provided-points`、四种派生周期、输入上限、参数范围和预热规则：

- MA：`max(period,60)-1`，现有 MA 输出包含 ma5/ma10/ma20/ma60。
- MACD：`slow+signal`。
- RSI：`max(short,mid,long)+1`。

这是计算能力，不能据此宣布真实年度历史或预热已充足。Server 必须按实际覆盖决定可得集合；预热不足保留 null，不能补零或套用日线值。输出时间可以使用等价 UTC 偏移形式，消费端按时刻关联显示 bar。

## 共享夹具与验证

本仓 `tests/fixtures/thesis_ledger_period_indicator_v1.shared.json` 与主仓 `packages/schemas/fixtures/market-period-indicator-v1.shared.json` 使用完全相同字节；能力夹具同样成对冻结。它们来自主仓 Domain 的受控工作日日历及合成价格，仅证明跨运行时协议与真实数学计算，不证明交易所或在线 Provider。

主仓通过 `scripts/print-market-period-indicator-fixture.mjs` 输出受控 payload；正式更新两仓夹具时必须核对 SHA-256。每个周期都保存聚合编码及请求，以便核验上下文和点摘要的关系。

激活本仓虚拟环境后执行：

```bash
python -m py_compile api/thesis_ledger.py src/services/thesis_ledger_period_indicator_contract.py tests/test_thesis_ledger_period_indicators.py
python -m pytest tests/test_thesis_ledger_period_indicators.py tests/test_thesis_ledger_indicator_calculation.py tests/test_thesis_ledger_market_v3.py tests/test_thesis_ledger_contract.py -m "not network" -q
```

## 兼容与回退

旧日线请求、Data V3 handshake、原始 Provider 路由和指标公式保留。没有新环境变量、依赖或数据库变更。主仓尚未完成真实聚合装配时，公开派生请求继续明确降级；DSA 计算能力不能替代 Server/图表完整验收。回退时由版本发布入口恢复对应应用代码，保留现行原生请求；不清理业务数据或卷。
