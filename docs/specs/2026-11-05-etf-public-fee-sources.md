# ETF有界公共收费来源规格

对应[任务](../archive/tasks/2026-11-05-etf-public-fee-sources.md)，跨仓产品边界见[主仓规格](../../../thesis-ledger/docs/specs/2026-11-05-etf-public-fee-slices.md)。新增范围为现有三市场官方规则Provider的ETF精确来源，不新增Provider或自动启用路由。

510300.SH的2023-08-28～29仅提供上交所竞价经手费；02800.HK的2025-08-06～11提供交易费、SFC、AFRC与独立依据的印花税豁免；SPY.US的2024-05-10～24提供Section31及按实际股数的TAF。每个包保留公开文件、摘要、实际观察时钟、覆盖说明及historicalAvailability=notVerified；ETF不得选择STOCK旧来源，日期和证券越界拒绝。

复用公共收费标准合同、网关和现有MultimarketRuleProvider；精确绑定、manifest、只读目录与来源探针一致。CN原四费项来源保持原样，ETF部分收费不补零。US既有T+2日历证据及收费日解释保持，ETF适用依据独立引用。新增公开材料读取预算沿主仓规格，失败不自动重发。

定向测试覆盖三个市场正例、资产／证券／日期／来源错配、金额维度、目录探针与合同JSON往返；再执行数据测试范围门禁，正式同步和目标HTTP由主仓任务对账。
