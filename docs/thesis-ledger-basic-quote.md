# ThesisLedger 基础报价的可空量额合同

## 本批行为

现有 Quote V3 保留全部字段名、来源与时间语义，只将 `volume`、`amount` 扩为数值或 null。缺失、占位文本、布尔值、负值、非有限值不能作为有效量额，公开响应以 null 表达；来源真实返回的 0 仍是 0。

HiThink 的 `price_change`、`price_change_ratio_pct` 并非基础价格消费的必需输入，缺失或非法时不阻塞该报价。现价、前收、开高低价格及其关系继续使用原校验，证券身份、认证、精确目标、路由修订和晚到结果保护不变。本批不取消 HiThink 现有来源准入，也不自动启用未配置或未准入路由。

适配器和 HTTP 边界复用 `src/services/thesis_ledger_quote_values.py` 的非必需数值规范化。公开字段投影归 `api/thesis_ledger_quote_fields.py`；现有巨型 API 入口只调用投影，不增长报价职责。网关只要求基础价格字段，不再以量额缺失拒绝整条报价。未知字段不能被下游用 `null ?? 0` 当成真实零值。

## 单位与时间

### 腾讯 ETF 基础报价

腾讯 Provider 现在另有 CN/ETF REALTIME_QUOTE 的精确 `tencent` 来源。单标入口复用既有腾讯封套/正文身份与时间解析，保留完整 `.SH/.SZ` 代码并校验市场前缀；不通过股票成交量或市值启发式推断 ETF 单位。量额目前返回 null，单位 unknown；价格与原上游时间可用于基础展示。日线仍使用原生三口径路径。

这一精确基础路由与已有基础价格一样按配置执行，不依赖临时人工审核记录；配置前后和晚到策略变更保护仍保留。股票 Quote、HiThink 来源准入、分红/因子及严格历史能力保持各自条件，基础报价不代表完整专业行情资格。

2026-10-06 已经通过正式脚本同步到目标可写层，并按标准策略保存启用修订37；510300.SH/159516.SZ真实Quote与159516详情页面通过，旧上游时间、陈旧状态和量额null保留。详情消费接缝同时升级为校验V3生效投影；部署范围、失败历史和页面证据见[腾讯ETF基础报价验收](../../thesis-ledger/docs/tasks/evidence/2026-10-06-tencent-etf-basic-quote.md)。此处是该次验收记录，后续策略状态以目标当前修订为准；镜像未更新。

ETF 量额单位未核实仍标 unknown，不借用股票单位、不推断倍率，也不授予量额敏感计算资格。来源时间为空或旧于抓取时间时，原 marketTime／fetchedAt／freshness／stale 继续传递；本批不改变新鲜度期限、行情日历或陈旧回退。

## 消费与部署次序

新消费者同时接受旧数值和显式 null；旧消费者可能拒绝 null。部署必须先更新主仓 Schema、Server 和客户端消费者，再更新 DSA。2026-10-06 已按 infra 正式 `sync-code.sh thesis-ledger`、`sync-code.sh dsa` 次序更新目标，运行产物和页面消费通过；这是可写层快更，容器重建后需重新同步。现有策略未配置实时 Quote，没有修改真实路由、准入、凭据或数据库；目标与受控输入的证据分别见主仓本主题实施报告第七节。

不创建额外研究模式或全局质量平台。完整现金收益、分红事件、本地等差／乘法派生及历史收费不是本模块职责。主仓范围与任务见同级 ThesisLedger 的 `docs/specs/2026-10-03-multi-source-remaining-acceptance.md` 和对应 Task 文首 PR 队列。

## 验证入口

`tests/test_thesis_ledger_quote_values.py` 覆盖非必需数值和真实适配代码；`tests/test_thesis_ledger_hithink_quote_runtime.py` 贯通测试用来源响应、临时 SQLite、现有鉴权网关与 HTTP 序列化。既有准入、撤销、时间和错误映射回归保留。主仓独立测试覆盖 Quote Schema 和 Server 缓存中的 null／数值、身份与关键价格错误。

这些是受控输入的代码验证，不是新的在线数据源可用性或部署验收；执行次数与结果在主仓本主题 evidence 记录。中文专题为新增的下游专属合同，未改中英 README 或创建英文正文副本。
