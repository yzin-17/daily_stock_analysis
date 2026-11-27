# 按现有来源自动获取报价

[已完成任务](../archive/tasks/2026-11-06-background-quote-source.md)。风险规则自动接入由 ThesisLedger 管理，DSA 拥有来源执行与限制。

Data Token 新增 POST `/api/v1/thesis-ledger/market/background-quote/plan`（零上游）和 `/market/background-quote`。计划接收 contractVersion=1、consumer=thesis-ledger、requestId、规范 symbol、assetType，返回 generation、policyRevision、target、acquisition、reason、minIntervalSeconds、sourceUpdateIntervalSeconds。未知来源更新频率保留 null。

当前首个 eligible 目标决定后台资格。腾讯沪深 ETF 使用 `public-snapshot`。富途港美股票与 ETF 使用 `account-snapshot`，须由目标 OpenD 确认市场实时行情权限，业务执行在同一 OpenD 连接复核权限后才读快照。已准入的 HiThink A 股和沪深 ETF 精确快照使用 `account-snapshot`，沿用当前有效凭据。执行携带 generation，标准 Runtime 在读取前后复核当前代次与顺序，只使用计划固定的目标。

Server 市场会话门禁与共享请求窗口继续适用。股票报价缓存窗口为 15 秒，ETF 为 600 秒。DSA 按富途官方快照接口的每 30 秒 60 次上限，对同一 OpenD 连接地址的快照读取联合作用限额。上游更新频率保持未知。

同一 DSA 进程内，同一 HiThink 凭据的股票和 ETF 快照共用请求预算，交互和后台复用同一个适配器。每次只发一个请求，采用回包额度，余额不足一笔时暂停。HTTP 429 或业务码 4001 依据 `Retry-After` 退避。额度耗尽或限流没有有效提示时，采用有界本地指数退避，到期放行一次探测。上游额度计量窗口及快照更新间隔保持未知。

Yahoo 的后台资格继续以已确认的自动采集授权和请求额度为前提。

现有 GET quote 完全兼容；计划和执行均不修改策略、凭据或用户任务。真实上游和部署单独验收，不由离线测试代替。本专题只新增中文合同，未修改现存双语正文。
