# 按现有来源自动获取报价

[Task](../tasks/2026-11-06-background-quote-source.md)。风险规则自动接入由 ThesisLedger 管理，DSA 拥有来源执行与限制。

Data Token 新增 POST `/api/v1/thesis-ledger/market/background-quote/plan`（零上游）和 `/market/background-quote`。计划接收 contractVersion=1、consumer=thesis-ledger、requestId、规范 symbol、assetType，返回 generation、policyRevision、target、acquisition、reason、minIntervalSeconds、sourceUpdateIntervalSeconds。未知来源更新频率保留 null。

仅当前首个 eligible 目标为腾讯、中国 ETF 时允许 public-snapshot，最短600秒沿用持久来源/标的预算，失败不释放；其他来源 blocked，不自动改路由、解冻受限调用或探测权利。执行另带 generation，标准 Runtime 在选定目标前、返回前复核当前代次与顺序，只保留精确腾讯目标。缓存与预算不构成上游更新 SLA，不声称流式或捕获所有瞬时越界。

现有 GET quote 完全兼容；计划和执行均不修改策略、凭据或用户任务。真实上游和部署单独验收，不由离线测试代替。本专题只新增中文合同，未修改现存双语正文。
