# 股票历史状态按需读取实施记录

对应 [规格](../specs/2026-10-09-history-data-on-demand.md)，跨仓进度由 [主任务](../../../thesis-ledger/docs/tasks/2026-10-09-history-data-on-demand.md) 统一维护。

2026-10-14 TickFlow免费历史由主仓统一适配Task TF-F拥有：独立免费连接三个沪深A股日K绑定，原完整服务/Key/报价保持；六组实源、有限口径核对、正常新Run及断网重放已过。330项受影响及末次99项定向/边界复用；取消紧张期1个历史回滚底线后正式update.sh dsa退出0，新镜像d9cdba416f1f…及源码/6卷/新旧重放核对通过，TF-F1～F4完成。[真实证据](../../../thesis-ledger/docs/reviews/2026-10-11-unified-data-capability-runtime-evidence.md#record-tickflow-free-history)。本记录仅回链，不重复来源任务或RQ/Tushare外部验证。

2026-10-14登记资格修正见[主仓K37-C证据](../../../thesis-ledger/docs/reviews/2026-10-11-unified-data-capability-runtime-evidence.md#record-k37-entitlement-v2)。DSA仅接受新entitlement policy并去重五条公告事实：`data_provider/verified_cash_dividend_events_v1.py`由DSA现金事件核对领域拥有，版本明确，HiThink/东财各自匹配保持；不补供应商公告日/状态，不扩大核验范围。112项受影响测试通过，通用读取完成、默认具体事件核验范围仍有限。用户要求重试后正式update.sh all退出0，固定SDK编译及DSA新镜像固化通过；新容器实际加载共享五事实，新旧研究镜像后断网复现通过，[固化复验](../../../thesis-ledger/docs/reviews/2026-10-11-unified-data-capability-runtime-evidence.md#record-k37-entitlement-image-solidified)。

2026-10-13东财普通现金研究由统一适配层K37b-R收口：既有东财读取模块复用有界传输，新增单基金页面标准化，缺公告日/状态保持null及问题，单位按表头每10份精确换算；注册同一研究协议，旧严格路径保持。104项定向及能力边界通过；6次真实读取、五事件独立对账、3个新快照/Run、实际页面/下载和断网复现见[主仓证据](../../../thesis-ledger/docs/reviews/2026-10-11-unified-data-capability-runtime-evidence.md#record-k37b-research-v2)，不重做HiThink或未变化外部条件。

2026-10-13普通现金研究修订由[统一适配层 K37f-R](../../../thesis-ledger/docs/tasks/2026-10-09-unified-data-capability-adapter.md#k37f-r普通-hithink-分红研究执行包)维护。DSA复用原读取器，在新规范化版本保留未知状态及逐行问题、原业务响应、明确研究身份/币种依据和有限具体事件核对；旧D1～D5输入继续兼容。局部182项、严格/读取兼容63项、其余HiThink277项及语法/关键flake8/能力边界已通过；目标实源/消费者结果回查主任务，不建立第二份当前验收表。

- [x] S1：逐日规范化与 SDK 读取。精确写集 `src/services/thesis_ledger_baostock_daily_status.py`、`tests/test_thesis_ledger_baostock_daily_status.py`；旧 normalizer 和日级无 Bar 研究实现只读。验证明确停牌、有价无量、起点已停牌、跨月、缺行、空值、冲突、证券身份和失败。29 项定向测试与 `py_compile` 通过；尚未部署。
- [x] S2：仅在共享结构化状态合同就绪后接入 manifest、路由与缓存，具体写集由主任务收敛；不改变用户主备顺序。

## 执行方式与基线

用户后续要求不用子代理，本轮主代理直接实施。DSA 当前 207 项 dirty，保留已有同主题 WIP，不提交或推送。

## S2 验证记录

## S5-D：HiThink 股票研究读取

- [x] 读取接缝：`src/services/thesis_ledger_research_calendar.py` 精确目标识别、新增 `src/services/thesis_ledger_hithink_stock_read.py`、原 runtime 股票方法仅委派；不重写适配器或扩大通用读取合同。
- [x] 定向验证：新股票读取测试、研究日历及既有 HiThink 股票/runtime 测试。覆盖缺行允许研究、普通请求缺行拒绝、日历不可用拒绝及错误来源/ETF 不扩展。
- [x] 部署：infra `sync-code.sh dsa` 预检通过后执行，真实准备与新快照验收回写主 Task；本地测试不抵充。
- [x] S5-E：公共分页合同在 `thesis_ledger_market_v3_facts.py` 登记 runtime 已有的 HiThink 精确股票协议；HTTP 定向测试验证合法缺日及非交易日拒绝。其他目标不获得该协议。

实际认证网关与临时 Control 策略的 BaoStock 状态读取、缓存命中、未配置不读取、pin 不符、策略变更拒绝，以及原公告/生命周期/缓存合计 38 项通过；定向关键 flake8 与所有所属 Python `py_compile` 通过。SDK 全局连接使用独立进程和 45 秒总预算，缓存原子保存规范化结果并绑定实际观察时钟。没有修改通用执行器、用户主备、真实凭证或准入。目标运行态证据仍在主任务 S5。

S5-D/E 定向 96 项、受影响分页/日级可交易性 35 项通过；所属 Python 编译与关键 flake8 通过。原 runtime 方法按职责提取，未继续增大存量大文件；全部用户路由及凭证保持。

正式 `sync-code.sh dsa` 兼容预检和执行退出 0，目标 healthy，原镜像不变；实际两个股票跨月新回测均使用 HiThink 价格主源。此阶段 BaoStock 状态快照因公告主源部分成功未被选择；随后按用户授权交换股票状态主备，主 Task S5 已通过，价格主源保持 HiThink。


## L1：基础日期

- [x] L1-A：新 `thesis_ledger_baostock_lifecycle.py` 和专属测试；只复用单证券 SDK/日期解析，不新建全市场基础资料库。
- [x] L1-B：状态来源挂载和缓存修订，真实来源引用与失败原因；原日状态及旧缓存不回填。
- [x] L3（DSA 子阶段）：定向到必要相关检查后正式最小更新，实际基础日期正常读取/消费已回写主 Task；主仓跨上市日准备与摘牌价格窗口仍开放，不等于主 Task L3 完成。


基础日期接缝已实现：按单证券 query_stock_basic 保留 IPO/摘牌日期、独立状态与当前观察摘要；日级状态组合和缓存修订已接入，原日期/状态测试及新测试通过。该本地阶段尚未作目标部署与真实读取；后续生产证据见末尾，不将本地检查单独视为生产消费完成。


追加 L1-C：目标实际读取暴露基础日期不可用不应随日级有效状态长时缓存；仅状态来源文件与专属测试处理失败组合不写缓存及正常读取刷新，既有失败快照保持，目标仅 dsa 快更。


追加 L1-D：目标600221模块测得47.92秒，额外登录/登出导致45秒超时；生命周期helper仅复用一次SDK会话，原日状态规范化不改、期限不提高。专属会话测试与仅dsa正式快更后验证正常消费。


L1-C/D 完成：失败基础日期不长时缓存、正常下一次读取刷新；同一45秒有界请求复用一次既有SDK会话。相关48项通过、关键flake8通过，正式仅dsa快更退出0；600221正常路由及新v5回测取得IPO1999-11-25及空outDate，不再整窗回退。新旧快照不会回填。跨上市日准备与摘牌窗口价格的主仓验收仍开放。

## L3-D3：HiThink未识别证券的错误分类

- [x] 实现：新`src/services/thesis_ledger_hithink_stock_errors.py`拥有既有业务错误表与精确1002身份说明；原股票适配器仅委派，缩小原524行文件，不增供应商业务判断到主仓。
- [x] 定向验证：新`tests/test_thesis_ledger_hithink_stock_error_semantics.py`通过真实适配器传输接缝验证精确未知证券、错证券消息、其他1002、未知业务码和凭证不回显；原股票/runtime相关测试与语法、关键flake8保持。
- [x] 正式目标：infra最小`sync-code.sh dsa`预检后，再以相同只读凭证快照、证券/日期和原请求参数验证错误分类及普通对照。主Task摘牌真实消费仍开放，不能以负例分类正确替代。

当前只读观察2026-10-09T11:52:10Z：控制凭证配置3，600112.SH两个原窗口业务码1002/message精确Unknown thscode；600221.SH同2024-07-13～08-23参数取得30行。未改主备/凭证/准入；所有源码修改在此精确叶内，保留三仓并行WIP。

完成证据：`tests/test_thesis_ledger_hithink_stock_error_semantics.py`与原股票适配器47项，所属股票读取/runtime28项通过；精确`py_compile`、`scripts/ci_gate.sh syntax`与`flake8`通过。原股票文件524→511行，不继续增加存量规模。infra目录正式`sync-code.sh dsa`预检及执行退出0、目标healthy、镜像不变，仅可写层更新。2026-10-09T11:59:35Z目标相同只读探针中600112两个原窗口均symbol_not_found，600221仍30行，请求参数与供应商业务状态不变，凭证configVersion3/policy74保持。摘牌价格真实回测仍未通过，主任务保持开放；不扩大为全体摘牌证券不支持的结论。

## D1：HiThink已公布分红方案的研究规范化

- [x] D1-N：新纯`hithink_fund_dividend_semantics.py`拥有显式研究版本/进度解释/全空识别，既有normalizer委派；专属`test_hithink_fund_dividend_research_semantics.py`验证默认隔离、未支付语义、单位日期、未知字段与冲突。
- [x] D1-P：仅reader和专属`test_hithink_fund_dividend_placeholder_read.py`接入显式版本的count0/单全空兼容；错误计数、默认入口及传输上限保持。

共享文档串行更新。主仓拥有研究/现金/冻结协议，DSA仅实现供应商字段差异；本轮不改mapped-read、runtime、路由、身份或准入，正式部署和真实标准消费保留主Task D3～D5。局部源码与确定性真实响应消费不代表生产回测通过。测试从定向到所属回归及ci_gate语法/关键flake8，不跑无关全量。

D1-N本地完成：新研究语义31项与原规范化16项合计47项通过，默认未知进度拒绝和文字实施事实保持；生产接缝尚未接入。接续D1-P，仅修改声明的reader与专属测试。

D1全部本地检查完成：最终6文件106项通过；仓库syntax/关键flake8通过，最终错误文案修正后精确5文件编译/关键flake8通过。现有mapped-read及默认读取不启用研究参数，原数字进度拒绝保持。D0取得的51条真实行本地规范化为方案观察，3证券有界窗口各2条，159516空行忽略1；覆盖false、数字进度现金事实0、联网0。证据`/tmp/history-demand-d1-real-response-normalization.json`，主仓统一Task记录完整命令和来源摘要。

未部署、未接入标准研究现金能力或回测；本地接缝完成不代表运行态消费通过。跨仓研究协议、准备/冻结、结果/重放仍由主Task D2～D5维护。CHANGELOG仅新增一条，双语首页未变，供应商接口、用户配置和准入未变。

## D3：标准方案观察读取（本地实施与目标验收分别记录）

主仓原Task为跨仓唯一验收账；此处只记录DSA叶。启动合同为主仓market-known-cash-wire-v3，跨语言样例market-known-cash-v3.exchange.json；币种和身份依据属于显式研究范围/请求绑定，不宣称响应回显。

- [x] D3-RH：只拥有src/services/thesis_ledger_hithink_known_cash.py及tests/test_thesis_ledger_hithink_known_cash.py，复用D1 fetch/normalizer、固定凭证快照、必要字段/单位/独立日期与范围筛选，生成通用方案观察及真实来源摘要/时钟。定向读取及原D1回归、精确编译/lint后停止。
- [x] D3-RE：只拥有src/services/thesis_ledger_known_cash_v3.py、tests/test_thesis_ledger_known_cash_v3.py及api/thesis_ledger_events_v3.py的显式协议分派，按当前有效策略/目录/Provider启用/删除/凭据/依赖状态固定来源，读前后变化拒绝，旧事件分派保持。共享JSON样例、真实FastAPI鉴权端点与原事件回归、所属语法/关键flake8后停止。

以上叶串行维护原Spec/Task/CHANGELOG，正式更新与有界真实标准读取独立归主仓D3-T，准备/持仓/结果/现金快照不属于DSA叶，不把本地测试算作已部署或生产回测通过。

D3-RH本地24项读取测试通过，与D1显式语义/占位回归合计74项；D3-RE40项控制/鉴权/错误测试，连同读取24项、旧事件15项、旧HiThink11项及mapped-read10项/控制1项共101项通过。严格请求时钟补充后相关90项再通过；安全原因结构化增量后端点55项通过。ci_gate syntax/关键flake8及最终5文件精确编译/关键flake8通过。共享样例由主仓定义，输出身份/币种依据不冒充响应回显；主备/凭证/准入未修改。正式部署与实际读取独立记录主Task D3-T，当前本地结果不能称现金生产回测。

D3-RE-F收尾：直达入口补eligible/reason一致性与禁用/额度/凭证/依赖状态拒绝。首次新增5项，随后统一安全失败原因，保持主仓错误枚举；不将控制原因改成上游正文。D3-FP发布样例改为DSA自身tests/fixtures/thesis_ledger_known_cash_v3.shared.json，主仓组合测试核对同一exchange与失败枚举，DSA可独立运行。最终新控制46项+读取24项+旧事件15项+旧HiThink11项共96项通过；原mapped-read/控制11项先前通过且输入未变。精确编译/关键flake8通过。目标更新及运行态复验以主Task为准。

D3目标验收完成：正式sync-code.sh all及控制收尾的最小sync-code.sh dsa均退出0、预检通过、healthy且镜像不变；仅可写层更新。目标Server正常能力读取→已部署DSA→HiThink四证券读取成功，三正例各2条，159516零条/忽略1占位，覆盖false；主源位置0/策略74/config3保持。控制收尾后2026-10-09T15:56:34Z同一510300窗口2条再通过，供应商总5次。旧事件仍拒绝，标准交换离线网络0；主仓Task/补充报告记录完整源摘要/窗口/脚本及边界。无现金run/snapshot、持仓或结果页面接入，D4/D5开放。发布副本与单仓测试路径已收敛，无主备/凭证/准入写入或首页改动；源实现不依赖相邻主仓文件。

## D4-FD 只读复核执行叶（2026-10-10）

依赖主仓D4-FC已验证合同；写集与停止点以主仓D4-FD为准。新增控制快照所有者/复核服务及专属测试/发布样例，迁移原资料服务的控制调用与供应商通用异常归属，鉴权API仅新增独立协议分支。旧研究读取用同一控制快照，不重复供应商语义；无准入或配置写入。

- [x] D4-FD：精确合同解析、控制前后/预期配置/HMAC/目标顺序/鉴权一致、零供应商调用的复核服务与FastAPI；定向新旧读取/Control回归、精确编译及必要flake8通过后本地收尾。
- [x] D4-FG：正式更新/实际只读消费，由主仓原Task拥有；本地测试不能替代。现金准备/快照/Worker和结果仍归主仓后续叶。

D4-FD本地证据：新28与旧已知/供应商研究70项共98通过；复用真实控制快照/原始HMAC，FastAPI鉴权、配置不符422、读取期间撤权401和旧分派通过，供应商调度0，策略/目录/Provider未写。必要旧事件/Control组最终18通过；首次唯一失败为原已有库存扩展68而测试总量56，主仓D4-FD-T只修独立精确期望集合，来源代码和原CN就绪断言保留。精确编译、ci_gate.sh flake8及测试增量critical检查通过。发布副本与主仓Schema测试一致；正式运行态与现金消费边界继续以主仓FG/后续叶记录。未改变项目首页内容，双语README不需同步。

D4-FG正式证据：infra正式sync-code.sh all兼容/结构预检和运行态更新退出0，镜像不变，只有可写层同步。2026-10-09T19:21:56.518254Z目标Server正常能力读取器实际核对已保存HiThink主源、公告备用位置及74策略/配置3/原凭证修订一致；请求expected配置改4正确拒绝configuration_changed，前后策略/目录保持。主仓原Task保存实际响应/日志；这是只读当前控制验收，不是资料读取、现金准备/快照/Worker结果或完整准入。
