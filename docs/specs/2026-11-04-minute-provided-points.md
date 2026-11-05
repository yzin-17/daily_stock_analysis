# 分钟派生周期指标输入规格

承接ThesisLedger的[分钟展示规格](../../../thesis-ledger/docs/specs/2026-11-04-market-chart-followups.md)与本仓[任务](../archive/tasks/2026-11-04-minute-provided-points.md)。Server负责从现有1m事实按已核验交易日历形成5m、15m、30m、60m展示点；DSA继续只接收provided-points，不查询来源或实现另一套聚合。

既有market-period-indicator-v1协议增加四种分钟周期声明，原生周期仍仅1m、1d。每次最多3650个点，数值、时序、输入指纹、聚合上下文与请求唯一性沿用原合同。分钟周期仅接受港美股票／ETF及none；缺量额不补零。MA、MACD、RSI公式、参数和预热声明不变。

验收包含旧周期回归、四种分钟周期、非法市场／复权、过期指纹，以及独立进程真实HTTP与TypeScript消费者协议核对。真实来源、Docker目标和客户端消费分别由主仓任务持有，受控HTTP不能替代。
