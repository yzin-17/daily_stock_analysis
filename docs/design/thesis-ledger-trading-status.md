# ThesisLedger 日级停复牌来源

## 独立风险警示与价格限制扩展

新增精确来源 `official-states / cn-stock-risk-price-limits`，读取独立生产包 `data_provider/trading_status/000995-2023-08-risk-limits.json`。窗口仍为 000995.SZ、2023-08-16～21；实际重读同一2023-029原件的三页，SHA-256为 `d98685515a9022db66f689cfbe782991575f3de03b0fc7d70c340170ba9b73a2`，实际核验时钟 `2026-10-07T06:18:28.685988+00:00`，原件153115字节。08-18开市起撤销退市风险警示，日涨跌幅5%改10%；其他风险警示按公告明确范围记录，16日的该维度保留unknown。

交易状态与风险/限制是独立维度。16日及19～21日的交易状态仍unknown，可同时保留已知风险与限制及对应文档引用；不会因此改为已知正常交易。旧日级停复牌包、内容修订及已知日期保持，新增来源不回填旧产物。新状态源沿用已有认证、精确目录、当前生效策略和本地来源读取，不需要新增供应商凭据；错误身份、范围、引用、限制比例、原价参照或提前观察时钟均拒绝。

主仓P6用显式原价规则研究消费限制，复权输入无转换证据时不应用；风险警示本身不制造停牌。扩展状态使用独立冻结版本，实际目标与页面验收由主仓 `docs/tasks/2026-10-07-p6-market-state-expansion.md` 维护，当前来源/消费者定向通过不等同目标完成。新共享响应 `market-risk-price-limits-v3.response.json` 来自本生产读取器，保留原件摘要与精确时钟。

## 原有停复牌切片

首个生产切片为 `000995.SZ`、2023-08-16～21，来源是皇台酒业通过巨潮披露的 [2023-029 公告原件](https://static.cninfo.com.cn/finalpage/2023-08-17/1217557800.PDF)。原件第一页和末页明确 08-17 停牌一天、08-18 开市复牌，文书及页面日期均为 08-17；没有可靠首次公开时分，不声明严格 PIT。

生产包位于 `data_provider/trading_status/000995-2023-08-suspension.json`，读取器为 `src.services.thesis_ledger_trading_status.OfficialTradingStatusProvider.read(request)`。身份采用现有 V3 data route，能力 `HISTORICAL_TRADING_STATUS`，Provider `official-states`，精确来源 `cn-stock-daily-suspension`。已注册正式网关和控制目录，主仓P5.4于2026-10-07完成000995、08-16～21的真实腾讯价格／独立状态冻结、执行、离线重放及Web验收；配置与实际边界以主仓活跃Spec／Task为准。

正式入口为 `POST /api/v3/thesis-ledger/market/trading-status`，要求现有 Data Token；Control Token 不能替代它。来源是内置本地输入，无供应商凭据或人工准入前置。精确目录按实际包是否存在报告 ready；调用只读取当前生效策略中明确选中的 target，Provider 不自行切源。配置在读取中变更时拒绝晚到旧结果。

本地收费和状态来源复用 `src/services/thesis_ledger_local_rules.py` 的精确来源匹配、就绪与错误适配；路由策略、调用预算和修订约束仍由原 runtime 拥有。读取模块通过调用方传入错误类型，不反向依赖 runtime。HTTP 共用 `api/thesis_ledger_local_rules.py` 的原认证/错误边界，公共收费原工厂入口保留兼容。

请求日期含起点和终点，响应按自然日覆盖整个请求区间。仅 08-17 返回 suspended、08-18 返回 trading，其他日期返回 unknown，包括周末和后来有真实交易的 08-21。unknown 没有事实文档引用；“supported”表示该有界来源能返回明确的已知/未知分类，不表示全部日期已知。错误标的、窗口超出支持范围、无效状态或来源引用仍给出对应错误，不能静默转成正常或未知。

单日读取不携带未来复牌日期；Runner已在独立冻结接缝读取状态，只给对应日期已有Bar附加停牌执行约束，策略不获得原始未来状态列表。缺Bar不生成价格，复牌与unknown不清除其他已有约束。包不应用公告中的 ST 风险标记、名称或价格限制变更，不把交易维度与这些维度合并。日级安排也不证明复牌后全日没有盘中暂停。

读取使用本地真实公告整理输入，不联网、抓公告或自行切源。版本为规范化内容摘要，采集时间单独记录；来源 URL、文号/标题、发布日期和发行人披露性质保留。主仓P5.3已实现独立快照和执行消费；P5.4另行验证真实目标任务，不能把单次来源读取成功等同于目标回测完成。

来源交叉核验时，通过已安装 BaoStock SDK 对 2023-08-16、17、18、21 查询取得 tradestatus=1/0/1/1，isST=1/1/0/0；基础信息 ipoDate=2000-08-07，API 均 error_code=0。该有界免费查询仅作为独立核对，不写入官方公告来源、不冒充历史公开时间，也不扩大本包已知日期。

首个目标标的的独立上市登记引用皇台酒业 [2020年年度报告原件](https://static.cninfo.com.cn/finalpage/2021-04-28/1209836916.PDF) 印刷第6页：2000-08-07在深交所上市交易。登记在既有 `thesis_ledger_market_v3_facts.py`，来源版本绑定该报告，`knownAt`按2021-04-28文档日期结束后的2021-04-29零点记录，不回填为2000年的首次公开时间。八月覆盖复用已登记的交易所日历，08-17仍是交易所会话；个股停牌独立由公告状态约束。上市／覆盖登记不证明已部署、价格无缺口或真实回测已完成，目标门禁以主仓P5.4为准。

共享 `market-trading-status-v3.response.json` 从实际生产读取器生成，Schema 位于主仓 `packages/schemas/src/market-trading-status-v3.ts`。固定响应版本用于跨语言对账；测试输入与生产包角色分开。来源阶段没有新增依赖、凭据、环境变量或数据库结构；后续目标代码按正式infra入口同步，仅更新容器可写层，不作为镜像发布。

真实任务 `c2a25e3f-09d6-419f-9323-e17bf2d98442` succeeded／attempt1／complete，checksum `c487baed3175fbf5`：17日无Bar／无成交，18日09:30买入、21日09:30卖出。整窗状态保留公告已知日期与其余unknown，收费窗口不足时披露使用原研究模型。目标Snapshot两次离线重放一致且网络0，原价格没有17日补价或状态回写；实际Web与格式2诊断下载通过。证据见主仓 `docs/tasks/evidence/2026-10-07-trading-status-target-acceptance.md`，仅覆盖该有界日级场景，其他状态、市场、完整历史与严格PIT另行验收。
