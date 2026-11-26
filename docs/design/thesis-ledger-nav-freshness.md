# ThesisLedger 净值新鲜度语义

## 历史与当前净值

`GET /api/v1/thesis-ledger/market/fund-nav/history` 成功返回并通过标准净值历史合同校验的点使用 `freshness=delayed`。`navDate` 是历史事实日期，早于当前时间七天不能证明历史数据失效，不再逐点据此标记 `stale`。来源切换记录在 `fallbackUsed`，不直接代表缓存过期。

单点最新净值继续使用既有时效规则。抓取时间 `fetchedAt` 与净值日期分别保留，不能用抓取时间替代净值日期。此调整不修改净值数值、日期、排序、窗口和限量行为。

## 缓存与诊断边界

ThesisLedger Server 的 last-valid 回退由真实刷新失败触发，其缓存状态与结构化失败原因由 Server 拥有。DSA 历史接口不因历史日期较早伪造回退状态，也不清理消费方缓存或修改来源启停。

## 验证与回滚

日期回归位于 `tests/test_thesis_ledger_nav_dates.py`，使用离线受控 Gateway 返回多个月前的有效历史，验证点均为 `delayed`，并保留原有 UTC 时间顺序。运行态更新使用相邻 infra 的维护入口；本地测试不证明真实 Provider 健康。回滚恢复历史接口原先的日期状态映射即可，不涉及数据结构或数据删除。
