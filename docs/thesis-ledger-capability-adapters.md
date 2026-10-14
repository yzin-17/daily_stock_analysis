# ThesisLedger 数据能力适配接入

本文说明当前已实施的静态接缝。进度和运行态验收统一见相邻主仓的[原 Task](../../thesis-ledger/docs/tasks/2026-10-09-unified-data-capability-adapter.md)；设计边界见[原 Spec](../../thesis-ledger/docs/specs/2026-10-09-unified-data-capability-adapter.md)。本文不维护另一份完成状态表。

TickFlow免费历史明确显示为`tickflow-free`／“TickFlow 免费历史”，上游`tickflow-free-history`，仅沪深A股1d及none/qfq/hfq三个绑定，排除B股、基金、指数及北交所，不把交易所后缀泛化为CNY币种。复用原SDK查询与日线规范化，固定`https://free-api.tickflow.org`、显式空Key（避免SDK继承环境Key）、max_retries=0；不需要新增环境变量或凭据。公开连接沿既有免凭据来源的启停默认，登记不会自动选择路由或请求上游；实际启停和主备仍由现有控制面维护。原`tickflow`的Key与四绑定、报价保持，免费日K不授予实时/分钟/ETF支持。来源探测通过SourceProbePolicy复用同一日线处理器，保留探测工厂与正常读取隔离；本轮没有额外实源探测请求。窗口不超过3650日、最多10000行，达到计数预算拒绝完整声明；单位缺乏通用可信说明时保持unknown。当前执行与有限样本只由原Task TF-F记录，原快照不改写。

## 已有能力增加来源

来源默认运行依赖由SourceDefinition.dependency_modules拥有；已有报价目标确实只走独立HTTP路径时，由该QuoteBinding.dependency_modules声明模块元组覆盖，不在公共依赖检查添加providerId分支。AK ETF/eastmoney报价沿原requests路径，股票报价/日线仍需AK SDK；无目标准备沿来源默认，模块就绪不等于凭据/准入或数据可用。

精确只读检查可通过来源`exact_probe_read`声明复用原读取器，入口传已绑定key/target及可选的实际policy_revision，并校验标准symbol/start/end样本，不传Runtime或任意SDK方法。CN费用、HK/US费用及盘中状态、公告状态和公告现金已按此本地迁移；来源拥有数据包路径/原reader与字节前后核验，公共官方前缀与文件分派已退役。来源级回调返回None表示委托未迁移路径。RQ/HiThink/Tushare/东财六在线事件诊断已由EventBinding.probe_read本地声明，执行成功返回None即完成本次读取，缺声明则not_adapted，失败不落回旧供应商分支。新来源不能依靠名称前缀获得检查或生产资格。公告七个独立功能诊断已在本地复用各自标准Schema/处理器/结果；缺有效实际修订则拒绝，返回前复核同代次，不虚构占位政策。目标更新/真实检查见原Task；本地包读取成功不等于当前准入、消费或冻结资格。

当前已迁移选定腾讯/HiThink 日线、腾讯 ETF 即时报价、efinance 六项日线、Akshare 十二项日线、BaoStock 三项日线及独立状态/粗风险/基础日期、公告独立状态/风险/规则/阶段、pytdx 沪深股票未复权日线、TickFlow完整服务股票三项复权日线及免费服务三个独立绑定、Alpha Vantage 美股未复权日线、Finnhub 美股 qfq 日线、Tushare CN/HK 股票 none 日线、Longbridge HK/US 股票/ETF none/qfq 八项日线、Futu HK/US 股票/ETF none/qfq/hfq 十二项日线，以及 Yahoo 七项 qfq 日线和两项已有 Share none 参考窗口。原140目标保持，新增免费三个后共143个精确目标各自归入唯一来源绑定；精确组合与真实就绪/消费/冻结仍由原Task和来源矩阵分层维护。源文件以 `data_provider/thesis_ledger_sources/` 中各模块为可运行参考。

efinance 股票/ETF、pytdx 股票和 AKShare ETF 即时报价也已归来源绑定。efinance 股票只读原单标快照，保留 TTL 缓存/限流与 ETF 冷却；pytdx 使用原单标 session 并校验证券及市场，缺失量额和未证明单位保持未知。AKShare ETF 复用原 4.5s 单证券 helper；本地修订与上游修订分别维护。

AKShare 股票 Eastmoney/Tencent/Sina 三项报价已绑定，来源模块保留各自缓存、HTTP、熔断和错误。Eastmoney 一次适配调用可以有零次或最多两次 SDK 请求，内部市场分页未导出；Tencent 原成交量投影含启发式，不能当作单位已核验。Sina 适配器严格核对唯一响应封套，缺来源时钟仍为未知。旧无目标 smoke 的 Sina 默认只由对应报价绑定声明，显式用户路由始终严格匹配。

HiThink 两项快照报价已归来源绑定和工厂，SDK 字典由来源投影为标准报价结果，原修订/4.5s/单位/时钟/错误 retryable 保持。Runtime 使用 Guard 已核对的同一凭据快照，并再次核对政策/凭据代次；现行配置读取不要求旧人工准入。旧修订与枚举 helper 从绑定派生，所有现行报价均通过绑定执行，公共分发不透传未登记 SDK 方法。

最新基金净值 FUND_NAV 已由 AKShare/EF 两来源共享强类型请求、标准点/结果和关联校验。SDK 列名/DataFrame 仅在 Eastmoney 适配器处理，Runtime/API 消费标准结果；完整来源先校验再取最新、未来估值日仍拒绝。原分页/55s/错误与旧 API 形状保持。估值日及 UTC 兼容日期编码不表示公告可见时间，身份仅请求绑定、公告可见性未核验；历史净值已使用下文独立序列合同，研究/原文/冻结协议继续各自原路径。

普通历史净值 FUND_NAV_HISTORY 也已由两来源共享独立请求/序列合同，复用标准点。完整 SDK 序列先验证、后截窗取尾，原报告日与 UTC API 边界顺序保持，API 无 SDK 列名。源错误与请求错误分别管理；SDK/原文和精确研究/冻结协议保持原路径。NAV 精确检查/smoke 现也复用同一绑定与合同，公共 Runtime/诊断的 SDK列名兼容方法已退役。适配器保留选择前最大报告估值日用于原检查未来日条件，不作为覆盖证明；精确 probe 的原样本日范围、独立工厂与缓存隔离保持，研究/浏览器/真实检查仍分别验收。

普通 FUND_HOLDINGS 查询已由 AKShare 精确绑定读取强类型持仓合同；来源适配器拥有原中文字段、季度、证券身份和百分比转换，完整序列先校验再选择最新季度。现有 API 只投影标准模型，旧权重排序、摘要和披露日未知保持。来源当前年/上一年空结果回看保持最多两次 SDK 调用，非空坏数据立即拒绝，不是切换来源；业务查询不再读取 DataFrame.attrs。精确检查/smoke 现也复用标准绑定，Runtime 原持仓 SDK透传/列名校验已退役；独立 probe 工厂和正常缓存隔离保持，SDK投影前错误沿原分类。目标未选持仓路由，真实消费仍待单独验收。

当前 CN/STOCK CHIP_SUMMARY 已由 AKShare/Eastmoney 与 Tushare 两个精确绑定读取同一标准估算摘要；来源适配拥有原 DTO、有限价格/成本区间/明确0–1比例/报告日和身份。两来源方法分别保留，不称原始持仓或跨源等价。原 SDK 算法与限流/年度或日历条件、Tushare工厂30s正常/5s检查、HMAC环境凭据缓存与控制快照不缓存保持；API删除按大于1猜比例，原字段和日期UTC回显保持，精确检查/smoke同合同。目录和测试不替代真实消费。

Tushare ETF none 专用日线已绑定同一现有日线能力，复用原精确SDK及4.5s/366日分段。原分段与完整头部验证归来源适配；DailyRead保留实际日期分段数，公共执行器删除ETF供应商特判，不修复坏证明。源传输完整不等于交易日覆盖，原量额转换保持。唯一精确组合可兼容旧分页证明省略复权，多组合仍须显式选择；完整证券代码保留至SDK身份核验。HiThinkETF专用路径已在后续子叶完成来源绑定。

HiThink ETF qfq专用日线现也由来源绑定执行，原完整/交互读取、日历/上市证据、稀疏交易状态及既有单位合同保持。Runtime专有closure/helper退役；公共日线执行与精确检查仅走标准绑定。来源请求元信息由DailyBinding派生，原修订/未知上游版本不变。错误保留code/retryable/安全diagnostics，交互来源不反向依赖Runtime；旧服务只投影来源错误。来源声明无目标准备工厂保留HiThink原凭据复核次数/错误边界，Tushare复核工厂缓存声明保留原HMAC缓存。现行其余目标也已分别绑定，原SDK及冻结/研究门禁继续独立。

公共费用六个精确目标已使用费用专有请求/结果合同与三官方来源绑定，复用原只读包读取器、十进制费率、范围/原文/摘要和收费日政策。原参考数据分派优先标准费用处理器，已迁移目标/工厂旧登记退役；港美交易状态已在后续子叶标准绑定，底层原读取器保持。标准模型验证完整左闭右开分段、身份和文档引用后投影原V3 JSON，不推断历史可用性，不添加路由或费用/研究假设。原金额进位、TAF与T+2条件保持；真实目标/冻结门禁单独记录。

旧聚合HISTORICAL_TRADING_STATUS七目标已进入独立强类型请求/结果与来源绑定，复用原公告包、BaoStock隔离/历史缓存/基础日期和港美盘中读取。公共local_rule_operation供应商分派及七旧登记退役；独立五能力保持原模型/读取。标准结果核对逐日完整窗口、来源/文档/结构化引用、覆盖未知数组与独立基础日期；来源级守卫保留旧BaoStock基础记录口径和港美整日unknown。旧V3响应/冻结字段不变，不将outDate改为最后交易日、不扩大研究资格。

1. 在 `data_provider/thesis_ledger_sources/` 新增来源模块。模块拥有自己的清单构建函数、工厂、凭据声明、运行依赖、精确绑定及实际适配修订。工厂只创建该来源实例，不选择备用来源。清单构建时的凭据导入保持惰性，避免控制清单和凭据定义的导入环。
2. 对已有日线使用 `DailyQuery`、`DailyRead`、`DailyBinding`；即时报价使用 `QuoteQuery`、`QuoteBinding`。处理器只接受能力专属参数和 `ReadContext`，复用已有原文解析器；证券格式、字段、明确单位换算及供应商错误分类在来源内完成。`DailyRead.frame` 是既有 V3 执行器的内部桥，产品响应仍由现行 V3 Schema 校验。
3. 为每个真实有效组合分别建立 `ExactKey` 和绑定。日线目前仅表达 `1d`，不能把资产/复权数组的笛卡尔积当成支持事实。传输有分段要求时在绑定提供 `verify_transport`，实际完整性、具体证券覆盖和独立交易日证据仍分别检查。
4. 在 `src/services/thesis_ledger_capability_sources.py` 增加一次显式来源登记。清单、精确目录、修订、分页、依赖和凭据查找视图从该定义派生；无需增加 Runtime/Control 的供应商执行分支。已有条目迁移时同时删除对应中央表条目，避免双重执行登记。
5. 增加隔离合同测试，并经过真实 `ThesisLedgerControlStore -> ThesisLedgerProviderRuntime -> execute_market_bars_v3` 和现行 HTTP 投影验证。网络替身替换上游原文，不能跳过政策、执行器或结果校验。`tests/test_thesis_ledger_capability_registry.py` 的新来源案例展示最小改动面。

登记只声明可适配能力，不写用户路线、顺序、启用状态、凭据或准入。真实就绪仍取决于依赖、配置、权限和当前政策。SDK 若有新增运行依赖，须维护实际依赖清单，并走 infra 完整更新入口。

`SourceDefinition.factory_scope` 默认 `route`，用于能力专有工厂；只有同一工厂能服务该来源全部能力时才声明 `provider`。后者进入原 Runtime 的凭据快照版本缓存，保留 SDK 客户端复用及原子发布；probe 沿原调用约定传入 `cache=False`，不能替换正常查询实例。TickFlow 是当前实例，普通 4.5s / probe 5s 预算经 `ReadContext` 传递，原批量配置仍由来源工厂传给 SDK。新增来源应测试重复读取、跨能力共享、换凭据和 probe 隔离，不自行创建第二套缓存。

Alpha Vantage 和 Finnhub 也使用原 Provider 工厂缓存；原精确执行器需要凭据重新核对时仍按原 `cache=False` 约定创建验证实例，不能把工厂复用扩张为所有实际数据读取共享 SDK。前者投影日期字典及编号字段，后者投影秒级数组并按纽约时区确定交易日期；两者使用既有多市场 HTTP 传输和错误边界。结构归一不证明未复权与 qfq 的真实价格等价，也不改变来源权限或消费资格。

Tushare 的 `require_credential_revision_for_cache=true` 保留原内部 HMAC 条件；无可用修订时不命中或发布工厂缓存。`ReadContext.probe` 只表示内部工厂探测目的，来源据此保持普通 30s/probe 5s，股票查询仍用原 4.5s 请求预算。不要通过预算数值猜测目的或泄露指纹。ETF/INDEX 分页、事件和 MappedRead 沿原读取与同一工厂，单次限流不隐式等待或重试。

内部 `DailyQuery` 已补齐现行 Data V3 的 INDEX 类型；只有精确绑定才能执行。Yahoo 当前仅登记原 US INDEX qfq 目标，指数转换和 ^ ticker 检查保持；其他来源不因此获得指数资格。两个 Share 窗口由同一来源定义分别绑定，复用原参考包读取器；原摘要、时钟、证券/窗口限制与消费者条件保持，不能套用通用 qfq 日线或继承其他窗口的覆盖。参考包不是当前在线数据证明。

Longbridge 来源工厂保留 legacy 三凭据与原 `build_page_oauth`。内部 `ReadContext.database_path` 供原 CAS 刷新仓储使用，其他来源无需提供；probe 传 `read_only=true`，不保存刷新或启动交互授权。`allow_empty_environment_credentials=true` 仅保存原 SDK 环境发现行为，控制凭据为空仍拒绝；该声明不表示来源可用。正常工厂仍按不可变凭据快照缓存，精确读取仍沿原缓存调用约定。八项绑定保留原 SDK 日期、复权枚举、时钟和未知单位，不在适配器内新增回退。

Futu 来源复用原 `FutuMarketAdapter` 与 `futu_query_worker`，隔离进程仍为 4.5s；原 SDK 多页完成、游标/身份/窗口校验和 finally 释放保持。其 `DailyRead.pages_fetched=1`/wire `pagesFetched=1` 计数的是一次完整隔离响应，不是 SDK 页数；worker 没有导出内部页数，保持未知。不要按行数推算页数，也不要用该计数证明完整性。HK/US 股票/ETF none/qfq/hfq 十二项既有日线的来源绑定集中拥有原工厂、opend 凭据/依赖及修订；现行报价/当前资料/每手目标也已分别绑定，继续复用同一工厂和原读取器。

Native 日线生产查找/执行已退役，旧空表仅为迁移回归与防重门禁哨兵，不允许新登记或生产依赖。已绑定的非 HiThink 日线只读检查复用能力处理器与原 probe 工厂，继续核对来源、日期和错误；HiThink专有interactive检查已沿其绑定复用原语义，产品范围外原生分析继续自身路径。检查成功不自动写准入或证明历史消费资格。

## 首次增加能力

TickFlow/Tushare/Finnhub/Alpha Vantage 四项既有单标股票报价均已绑定。Tushare 原 rt_k/4.5s/限流/原始交易时间、TickFlow 单元素身份与 SDK 明确手转股换算、Finnhub strict/5s 保持。TickFlow 同次原响应缺量额保留 None；Alpha Vantage 来源适配器复用原 HTTP/sleep/strict 错误，校验 01.symbol，缺数值不默认零、实际零仍为零，日级交易日期不是精确来源时间。两项实际规范化修订为 v2，上游 endpoint 协议未变，SDK 原生分析未改。旧私有股票检查省略资产/来源时，仅在该标准股票键存在唯一绑定时按事实补齐，不维护供应商名单或调整用户路由。

Longbridge 四项和 Yahoo 七项既有报价也已来源绑定。QuoteQuery 仅补齐现行 US INDEX，其他来源没有新增指数绑定。两来源保留原证券桥接/Yahoo 去 US 后缀、strict_source 与 SDK 结果原型，None 先按原 not_covered 传递，最终错误仍由原执行器汇总；ValueError 保留 invalid_response。Yahoo 校验单标代码及已提供的 quoteType，LB 原报价只校验单标身份，不能声称已独立核验股票/ETF 类型。缺量额/来源时钟不合成，不读取历史或外部备用。

Futu 与 Longbridge 的 HK/STOCK 每手股数使用同一 LotRequest/LotResult/QueryContract，保留正整数、来源 URL、原观察摘要和 historicalChanges=unverified；数据仅为当前参考，不能填充无单位依据日线或历史交易规则。规范化/整数/摘要由能力值模块拥有，原 SDK 经兼容导出复用；来源各自拥有真实适配与 URL。Futu 4.5s 隔离及 24h 缓存、LB 缓存观察时刻/板块身份与原工厂保持。公共读取与 HTTP/probe 不再分支供应商，不允许额外历史/复权参数；主备仍由原执行器负责。

Futu 四项 HK/US 股票/ETF 报价使用既有 QuoteBinding；原 worker、单标身份、价格/量额、来源时钟与释放保持，缺来源时间不以抓取时间补齐。公共报价分发按精确绑定拒绝不匹配的 upstreamSource，不能隐式调用旧通用接口；只读检查传标准证券代码，不维护第二份供应商格式名单。四项当前资料也使用独立 StaticRequest/StaticResult/QueryContract，原整数精度、未知字段和 historicalChanges=unverified 保持，HTTP/probe 复用合同；每手也已按下文标准合同绑定。

QueryBinding 的 `admission_required` 为可选内部 bool 事实，None 保持存量由 gated 推导的规则；Futu 当前资料为 false，目录 gated 仍为原 true。按配置读取和目录兼容标记分别表达，不因此写入用户准入或跳过凭据/启用检查。来源错误可传原 retryable 标记，默认 false；标记不增加重试。当前资料只接受规范证券/类型，不接受历史参数，其观察不能填入历史基础日期或价格覆盖证明。

QueryBinding 的合同必须同时提供 `validate_binding` 和 `correlate_result`：前者在上游读取前核对本能力请求与精确绑定，后者在 Schema 校验后核对请求及固定目标。校验由能力合同拥有，适配器只负责来源结构转换；不把关联校验留给每个新来源自行复制。处理器只能登记 query；声明 subscribe 需要独立生命周期实现，现有纯盘口状态测试不代表生产订阅。能力版本须为正整数并使用相应 Schema，公共注册器不按供应商或固定版本号分支。已有日线/报价继续走各自专用入口，通用 query 不能绕过它们的精确规则。

主仓先建立本能力强类型请求/结果、Schema、请求结果关联校验和共享样例，再建立 DSA 的严格合同及小型处理器。使用显式 `CapabilityContract` 与 `QueryBinding` 关联本能力；不能使用任意名称、任意参数的方法透传。能力专属参数不得堆入全部 RouteKey。

隔离 `ORDER_BOOK` 是当前参考：主仓 `market-order-book.ts` 与 DSA `thesis_ledger_order_book_contract.py` 校验同一 JSON 样例；两类不同结构的输入处理器在 `tests/order_book_adapter_fixtures.py` 中登记。生产 `CAPABILITY_SOURCES` 不包含这些测试来源或盘口能力。增加该合同没有改写日线或报价处理器。

query 和 subscribe 共享数据语义，但生命周期分别拥有。仅实现 query 的绑定必须拒绝 subscribe。当前盘口的 `BookSession` / `BookSubscriptions` 只验证纯状态：初始快照、账号和权限代次、切源代次、连续序列、失效重同步、取消释放与有界缓冲。所有增量先用于重建完整状态，再允许 UI 合并；缺序列保持未知，来源 B 的增量不能作用于来源 A 的盘口。真实订阅需要另行完成生产传输、控制和产品入口，本轮没有登记生产订阅。

## 消费、错误与冻结

BaoStock独立状态/粗ST及基础日期查询由来源专属`baostock_query_process`隔离SDK全局连接，复用原读取/标准化及45秒子进程预算，超时终止并等待释放；两能力不共享服务进程login/logout。状态/粗ST仍按原控制代次/来源修订共享获取，日期为独立事实，原未知日期/覆盖和错误分类保持。原工厂、其他日线、来源/适配修订不变；本地子进程合同与真实目标组合冻结分别验收。该主仓专题接入指南没有英文镜像，未修改通用full-guide_EN。

Server 继续通过 `DsaDataCapabilityReaderV3` 和现行 Market Reader 消费标准结果，不 import 供应商模块。目录的可选 `barQuery` 描述整窗查询语义；它不证明跨源等价、覆盖或消费资格。基础备用还要求完整目录、精确且不同的两个保存目标、匹配复权、备用就绪、现行消费者范围以及执行时的政策和响应校验。

来源身份未识别、参数错误、窗口无覆盖、权限拒绝和暂不可用必须保持区别。HiThink 的精确 `Unknown thscode` 分类复用原修复；不把个例扩大为所有退市证券。BaoStock 生命周期不是其他价格源的覆盖证据，摘牌日不是最后交易日。CN 股票备用的现行兼容证明边界仍有效。

受保护的配置恢复可以在原 Provider 保存接口提交可选 `expectedConfigVersion`；SQLite 原事务内比较当前代次，不符返回 409 且没有写入。只切换 enabled 时保留 settings 和写入式凭据。产品政策保存入口的可选 `expectedRevision` 在原 PostgreSQL 行锁内校验，不进入 DSA Desired Policy 数据合同。旧调用不传条件时保持兼容；真实配置或故障操作仍按用户单独授权执行，未知提交结果不猜测所有权。

完整日线与图表 HTTP 入口共用 `thesis_ledger_market_errors_v3.py`：身份未识别投影为 `source_symbol_unrecognized`（404），参数错误为 `invalid_request`（422），权限拒绝为 `permission_denied`（403），认证失效为 `authentication_failed`（403）。Server 对应保留来源原因、状态与请求身份，不因这些错误新增重试或扩大备用资格。错误消息采用安全文案，不返回 SDK 原文、凭据或请求 URL。

目录变化产生新的目录修订，不能因文件移动伪造上游修订。旧快照不回填新字段、不重算旧结果；离线重放只消费冻结仓储，不调用当前适配器或读取凭据。严格在线旧消费者会拒绝新字段，更新顺序须先主仓消费者、再 DSA 生产者。回退到旧消费者前也须先恢复不含新字段的生产者。

五项独立功能使用 `QueryBinding` 的标准能力合同与派生 `querySupport`，参数支持在调用来源前校验。`DsaDataCapabilityReaderV3.routedStatusCapability` 是原 Server 的人工主备入口；组合读取由 Market 的 `readMarketStatusCompositionV1` 校验证券、窗口、必需字段及整组政策，不在适配器追加切源。共享获取只保存规范化同源数据，每个请求重新投影身份并检查政策/配置代次；来源目录或缓存命中不代表本次完整性。

新普通 STOCK 固定来源快照使用独立 `frozen-status-composition-v1` 产物，复用原 Builder/ArtifactStore/Runner；每种能力保留请求、目标、时钟、覆盖和分类失败。只有选择新交易状态时替代旧聚合交易读取；仅选择基础日期等其他功能仍保留原交易约束。新交易状态只按既有已知停牌规则采用，其他新事实作为观察输入，冲突保留双方。现有 lifecyclePreparation 不改路径。离线验证只依赖冻结配置/产物，不访问当前政策或凭据；目标组合消费与浏览器验收仍分别查看原 Task，不能由本地样例推定。

## 验证入口

在 DSA 仓库执行：

```sh
.venv/bin/python -m pytest -q tests/test_thesis_ledger_capability_registry.py tests/test_thesis_ledger_order_book.py tests/test_thesis_ledger_market_v3_pagination.py
.venv/bin/python scripts/check_thesis_ledger_capability_boundaries.py
./scripts/ci_gate.sh syntax
./scripts/ci_gate.sh flake8
./scripts/ci_gate.sh deterministic
```

后三个命令要求先激活现有虚拟环境。门禁检查反向依赖、已迁移中央表残留和测试能力进入生产目录。真实来源、目标服务、浏览器、冻结消费与本地测试分别记入原 Task；本地盘口合同通过不代表真实二级行情交付。必要目标更新只能从相邻 infra 使用既有 `sync-code.sh` / `update.sh`。

事件来源接入沿原event_v3入口使用EventBinding，声明强类型本能力请求/结果、来源读取、修订解析和原协议投影。事件执行上下文提供本请求的准入/安全回调，不持有Runtime或切源权；事件目录从绑定派生，不能同时登记current-data。公告现金来源复用原notice执行器，标准模型验证每份十进制金额、原文字节摘要、准入范围和时钟；原协议字段保持。修订依赖包字节时应每次解析，缺包返回None，不把导入时摘要或虚构版本作为当前事实。不能将公告现金合同的证据/完整覆盖要求直接套到身份映射或覆盖未知来源。

在线事件诊断的EventProbeReadContext只提供标准样本、固定能力、UTC data_as_of、只读准入副本与既有EventCredentialContext有限回调。公共层负责草稿/当前凭据相等、当前admitted/范围与读取后准入复核；来源自己的probe_read复用原映射读取器。RQ保留隔离账号工厂及4.5秒诊断预算；Tushare保留cache=false/probe=True及冻结凭据工厂；东财现金保留max_pages=2，拆分使用自己的映射窗口读取。一次适配调用不代表一页或完整历史证明。生产查询处理器、源修订、旧冻结格式与重放均沿原行为，未配置/未准入的真实来源不会因注册诊断回调自动可用。

东财现金已复用同一个CASH_DISTRIBUTION CONTRACT，共用标准请求、现金事实、准入范围及观察时钟关联。公告另有原文证据模型；东财保持无公告原文、无strategyVisibility及historical_coverage_unverified。原有界页面读取没有改变，页面完整获取不签发历史完整覆盖；观测providerRevision与准入接口sourceRevision分别保留。来源工厂延迟到准入之后，东财现金不调用无用Akshare SDK工厂。现行目标均由来源binding登记，查询/SDK页面/工厂次数不可混为一项指标。

RQData现金/拆分已来源绑定，现金复用同一合同、拆分拥有独立经济事实模型，两者共享固定请求/准入/时钟信封。事件绑定声明当前准入解析器，公共Runtime只调用精确绑定；来源经请求绑定的持久仓、准入、凭据、主密钥和工厂回调执行原映射/隔离读取。SDK初始化仍在子进程，30s/2000行与账号双重核验保持，身份原文/币种关系独立校验，原wire与覆盖未知保持。所有19个manifest和凭据定义派生于来源；需要保留密码字节的来源声明字段归一化函数，不能在公共Control添加providerId分支。当前现行精确目标均已本地移植；SDK调用次数不是已证明的上游物理页数，未配置/未准入来源不自动可用。

RQ账号修订及身份准入读取器的明确拒绝以来源有限异常表达，兼容原ValueError/固定安全文本；事件适配器投影为现有not_admitted。读取前拒绝零SDK、读取后失效拒绝晚到事实，其他SDK失败仍upstream_failure，不按供应商异常字符串猜权限/覆盖。该分类不授予账号或准入，不修改原修订和冻结。

东财拆分已复用同一Split合同，标准模型沿现行正比例/显式类型合同，保留可选登记日和dateMappingEvidence；RQ方向及ratio=1过滤是其来源规则，不能套到所有来源。原mapped reader按明确映射扩展源日期窗口，源适配投影复核原文/唯一映射，比例1和小比例原标签与字段保持，原观察时钟/未知覆盖不改。公共拆分执行退役；现行事件均已来源绑定。新来源接入不得把源折算日变成生效日或借完整分页签发历史覆盖。

Tushare现金已复用相同现金合同，原本地原文guard先于政策/目录/任何账号路径，来源通过绑定回调复用原mapped SDK与原Runtime缓存构造器。provider工厂作用域沿既有缓存，route作用域直接来源工厂，不按供应商名分派。原Decimal金额、conservative-day可见日、原文字节/字段与覆盖未知保持；公共标准时钟以整数秒/等宽小数比较，支持原最多1024位精度且不改字符串。标准模型可表达可见性不等于所有来源提供可见性，源投影须保留真实支持边界。当前现金均已来源绑定；目标账号消费、浏览器/新冻结单独验收。

HiThink现金已复用同一CASH合同，保留原api_key/X-api-key、原文先验、全部准入标的范围、environment/control不可变快照、HMAC和30s mapped预算；原毫秒日期/每十份转每份金额保持，无strategyVisibility或完整覆盖。源投影回原hithinkIdentityEvidence，公共事件入口与当前准入查找都从精确binding派生。当前19来源/140目标各恰有一个binding，全部现行精确目标本地迁入；这不授予当前账号、路由、覆盖或研究资格，H08控制/缓存、新冻结、真实来源与目标/浏览器门禁分别由原Task验收。

当前五类缓存通过原Data认证的`/api/v3/thesis-ledger/market/cache-context`读取政策/来源配置/凭据和精确绑定修订的只读投影；来源请求/结果仍从既有能力执行入口取得。新来源绑定无需添加公共缓存供应商分支，但需正确声明精确目标、适配/上游修订和凭据需求。不透明代次不返回内部HMAC，环境凭据变化在政策及配置版本不变时也更换代次；控制采样冲突拒绝结果。该接口不执行SDK、不添加用户路由、不授予准入，不能用上下文ready替代来源数据、历史覆盖或消费成功验收。

五独立功能的querySupport由本能力强合同验证并随精确目录发布，原产品路由界面复用同一资格事实显示参数范围，不按供应商名称猜支持；粗ST不解释为细分退市风险，基础日期不解释为最后交易日。目录客户端投影统一验证完整/部分/不可用信封，参数范围仍不证明实际窗口完整。缓存控制请求错误、控制事实损坏和暂不可用分别沿原V3代码归类，不把内部结果Schema失败报成用户参数错误。真实功能保存/消费与新冻结由原Task分层验收。

## 来源凭据修订与报价守卫

`SourceDefinition.credential_revision_kind`声明既有准入比较策略，`credential_revision_payload`提供有限的内部认证载荷构造器。HiThink/Tushare使用hmac，其他日线来源默认version；RQData事件沿原绑定准入，载荷仍由其来源模块拥有。公共摘要保持原用途隔离HMAC，源认证字段/权限校验和载荷字节由来源负责，指纹不返回公开目录。新增同策略来源只扩展来源声明和合同测试；不能从工厂缓存许可推导准入或泄露凭据。

报价需要原政策/凭据前后核验时，声明`quote_guard_policy`：prepare接收类型化QuoteGuardContext，返回不可变快照与有限still_current复核器；缺资格返回None。公共Runtime在预算领取前准备，工厂使用同次快照，原读取结束复核，沿原not_admitted错误和来源消息。HiThink复用原Guard；无守卫的来源保持原查询生命周期。此回调不接收Runtime、不执行额外数据查询或隐藏切源，且不表示支持订阅。合同测试须覆盖准备拒绝时零预算/工厂/数据请求、快照复用、单次读取后失效、原错误和主备行为。

兼容报价准入查找可声明`quote_admission_reader`，类型化QuoteAdmissionContext携带同次可选快照；只在精确报价绑定存在时调用。HiThink保留原全部scope/有效期/时钟比较，不把该兼容准入变成普通配置报价的新门槛。日线修订直接从唯一DailyBinding派生，通用Gateway继续拒绝缺显式bar目标的旧日线请求，不再包含供应商SDK死分支。SDK页数、数据时钟、覆盖和上游修订沿各来源原合同，局部目录收敛不补造这些事实。

只读连接诊断可声明`probe_policy`，接收规范能力、同次凭据快照和既有代表证券；来源只执行固定SDK诊断，原安全错误由公共smoke桥接。`instrument_types`只覆盖有效精确能力/资产的健康记录维度，不生成查询/订阅目录或准入。HiThink三原能力已迁入，保持固定代表日/证券、4.5s和原SDK；draft不写健康/配置，非draft保持原健康记录。

依赖原adapter的诊断声明`adapter_required=true`及有限`capabilities`，公共smoke仍用原`_adapter(cache=False,probe=True)`；注入实例不读凭据，真实工厂只读一次快照，不命中或覆盖查询缓存，上下文不接收Runtime。Tushare/TickFlow报价代表证券及STOCK/upstream参数由来源拥有，继续同一精确QuoteBinding；Finnhub/AlphaVantage保持AAPL.US、days=5、strict=True及原整帧校验。未迁移能力继续原通用入口。诊断成功不能证明具体历史覆盖或订阅资格。

旧宽manifest确有来源专有兼容时可声明`catalog_manifest_policy`，只接收CatalogManifestContext（当前manifest/Key/Target、精确日线绑定事实），返回bool或None；非法返回失败关闭，None继续原检查。HiThink现金与股票价格兼容已迁入该来源，报价仍走标准精确匹配。新来源通常复用标准manifest检查，不需要声明兼容策略；该策略不执行数据、凭据或网络操作，不授予准入，也不替代唯一绑定登记。

政策准入确有来源后置复核时声明`policy_admission_guard`，有限`PolicyAdmissionContext`包含本目标key/target/manifest、已读取payload/state及绑定该来源的零参数`read_credentials`，不接收Runtime或SQL连接。公共层保持基础配置短路、事件当前准入检查及SQL状态顺序；守卫明确返回True才继续，未声明则原状态处理。HiThink现金保留事件身份检查后的第二次凭据/修订复核，未准入不读额外凭据；已绑定报价/日线基础路径不执行该守卫。不增加SDK调用、保存资格或放宽消费协议。

原研究日历资格由精确DailyBinding的`research_calendar_eligible`声明，接收冻结`ResearchCalendarContext`的symbol/start/end/market/asset_type，明确True才允许原日历补证入口。公共层先要求原assume-untradable-no-bar模式并查完整精确绑定，旧覆盖窗口优先；未声明目标不取得资格。CN原证券校验、Yahoo原身份与两有界包的窗口/观察时钟由各来源保留，不推断上市日期/最后交易日或完整价格覆盖。添加绑定不是放宽研究协议；新的协议仍须产品契约决定。零量和身份事实边界在各自迁移前继续原路径。

来源对已有结果确有原研究投影时，可在对应DailyBinding声明`research_frame_projection`；有限`ResearchFrameContext`仅有symbol与旧内部桥接帧，返回类型化`ResearchFrameProjection`，不传原请求、工厂或任意SDK方法，不执行查询或切源。Yahoo原零量响应摘要/元信息由来源适配模块解析，公共入口只选择绑定/原模式；正有限OHLC、金额一致、日期去重/摘要检查、原帧不改及重复投影保持。仅原HK/US股票qfq和00147.HK有界none声明；US none不自动过滤。旧V3事实/冻结格式保持，桥接帧不作为新产品能力合同。

原有界研究身份/单位通过对应DailyBinding的`research_instrument_policy`声明，有限`supports_symbol/read`接收标准证券身份与`ResearchInstrumentRequest`，返回`ResearchInstrumentFact`。公共原身份/依赖入口只选择精确绑定，来源保留原包、窗口、单位/价格网格、观察截点与错误。结果沿主仓InstrumentFact的研究观察子集：HK/US股票及对应币种，整手/价位成对已知正十进制字符串或成对未知，tradable=false、执行规则unavailable、原来源/摘要及字符串时间。公共关联校验拒绝错证券/市场/来源及晚于截点的时钟，比较复用精确时刻原语不截断纳秒。未声明不授予原价资格；qfq通用身份仍保留原未知单位/状态，不扩大协议、路由或准入。

通用`build_provider_manifest`只构造调用方声明，不按供应商删除资产。AK来源的Sina日线资产视图从自身Sina DailyBinding集合派生，交给原AK专属模板；原19份manifest逐字段及目标相等，其余旧宽声明/别名保持，不成为新增精确能力。来源例外不能写回公共构造器或新中央表；查询资格仍消费同一精确绑定/控制事实。

公开报价量额单位确需来源专有投影时，声明有限`quote_public_units`，接收冻结`QuotePublicUnitsContext`（已执行值/实际公开来源），返回强Schema `QuotePublicUnits`。来源解析原SDK单位并保留固定安全错误；API只校验标准模型，不按供应商识别字段。HiThink股票share/CNY与ETF量额unknown保持，坏单位仍原502；投影在Gateway和普通字段投影之后执行，不新增SDK、切源或重试。未声明仍省略units，现有公开枚举不因本片扩大。报价完整结果Schema/SDK别名按独立接缝核对，不把有限单位投影当成全部报价模型迁移完成。

报价SDK确有已报告来源别名时，来源定义以`quote_source_aliases`声明唯一规范化别名/本来源已绑定报价upstreamSource；公共HTTP按实际provider查声明，不维护SDK供应商表。枚举value/大小写归一保持；缺失/未声明/跨来源错报保持未知，不填入请求目标。Futu/腾讯/AK/EF/HiThink原有效别名保持，其他此前未报告来源不自动扩大公开身份。新来源扩展只更新自己的声明，测试注册不进入生产目录；拆分测试来源时应同步移除失去报价目标的别名。

报价读取结果统一为冻结`QuoteResult`强Schema，只复制价格/OHLC/昨收、可选量额/涨跌、原来源标签/标准已报告来源、时钟/陈旧标记及标准单位/有限错误。同快照推导昨收不再修改源对象，坏可选量额仍未知；源码对象与额外SDK字段不进入结果。Registry只读取一次，必要数值校验后纯投影来源/单位，Runtime二次校验保留标准元信息、不重做投影。单位失败先捕获为标准记录，仍在原HTTP普通字段之后报502，不改变切源。其他SDK时间仅以`source_time_observation`的unverified文本保存，不升级成provider_timestamp。新实现可直接返回标准结果/单位，其已报告来源必须关联本报价绑定；不填未报告来源。

日线公开单位使用精确`DailyBinding.public_units_policy`：`DailyUnitsPolicy`声明本来源私有合同名称及纯投影，`DailyUnitsContext`只提供已声明元信息、精确键和原请求，不提供SDK、工厂、Runtime或凭据。返回强Schema `DailyFieldUnits`，枚举复用现行价格协议；公开API只查绑定/标准模型。AK/Eastmoney原生合同与HiThink ETF核验范围解析分别留在来源，缺合同省略，错绑定/互斥合同/非法单位拒绝；原数值、单位未知、指纹、HTTP检查时机和冻结保持。隔离新provider已通过原SQLite政策、Runtime/Gateway和图表投影，各读/投影一次；测试声明不进入生产目录。HiThink原交互路径缺合同继续省略，不由完整窗口事实自动补造。

确有既有上游窗口限制时，精确日线绑定可声明`window_planner`，有限`DailyWindowPlanningContext`只有原请求范围、已证明会话与最大请求数；返回强Schema `DailyWindowPlan`，范围/预算/递进/会话交集由公共合同复核。来源规划器仅规划，不执行数据；原HiThink ETF五年算法只声明在原qfq绑定。公共调度查绑定，全部子窗先覆盖预检，再逐窗原执行器/控制检查，共享原4.5s总期限与8次上限，冲突即停、旧观察/分页/指纹保持。未声明或一窗仍原单窗，不将注册等同覆盖证明；隔离会话段规划的新provider复用相同HTTP调度及失败合同，测试不能代替多年真实来源资格。

现有Daily/Quote/Query/Event四类绑定均只有查询生命周期，注册必须为operation=query；能力合同即使允许query/subscribe，也不能将这些read函数标成subscribe。Literal注解不代替运行时校验；合法query可复用该语义合同，但resolve(subscribe)仍明确不支持。非日线ExactKey.adjustment必须为None，不能让route_key投影静默丢弃价格口径成员。能力专有参数继续由请求/资格Schema拥有，静态绑定不执行SDK、工厂或用户脚本，不新增生产订阅或宽参数路由。

同一capability/version必须复用同一标准合同：请求Schema、结果校验、请求/结果关联及操作等声明不能因来源分叉。注册临时视图从绑定派生，拒绝同版本冲突，不另维护中央事实表；等价显式合同可复用，合法不同版本分别登记。来源差异应在read或已有有限参数/适配策略处理。新版本不等于现有产品入口自动支持该版本，仍须实际Schema/消费验收；源修订和旧冻结不因合同查找变化而重写。

静态来源工厂的明确CapabilityContractError经同一有限执行边界转换，route/provider/unscoped不改其code/retryable；错误消息固定为安全工厂状态，不携带原文或凭据诊断。每次仍只调用一次原factory/ReadContext，失败不发布缓存，快照读取次数和原作用域保持；OAuth/Control和其他非标准异常继续原路径，不借此启源、改凭据或重试。内部错误构造器只用于公共异常转换，不传给来源，不导入SDK。

本地来源需要原目录就绪检查时，可声明`catalog_readiness`，接收已登记key/target的`CatalogReadinessContext`，返回bool或None。路径/文件及公告库兼容由来源拥有，公共入口只查精确绑定/声明并验证返回类型，不执行工厂或SDK；未声明或本能力不负责时保留原配置/准入路径。原十二个本地费用/聚合状态目标沿原检查，独立公告功能不自动取得同样资格。ready只表示当前入口可调用，不是证券/窗口覆盖、完整数据或准入证明，新来源不自动加入用户路由。

环境凭据声明`environment_credential_policy`：固定`read`返回原字段，必要`select_method`决定原认证方法，必要`snapshot_values`仅在最终运行快照附加值。一般方法直接复用本来源`credentials`首项；Config字段可复用凭据模块的`read_config_fields`，字段配对留在来源，不放公共供应商表。Tushare只在最终快照附加httpUrl；Longbridge保留原只读OAuth缓存解析与legacy/OAuth优先级；HiThink仍直接读环境。原配置/值读取次数、校验、页面坏凭据禁止回退、加密/HMAC和工厂缓存不变，策略不执行行情、保存凭据或刷新授权。测试新provider声明已证明环境快照无需修改公共方法分支，真实账号验收须另有证据。

## 独立状态能力错误合同

状态查询参数与ProviderCallError通过`api.thesis_ledger_market_errors.market_data_error_v3`生成`detail={contractVersion:3,requestId,error:{code,message}}`，与日线共用安全投影。来源实现只返回既有类型化错误，不写HTTP信封或供应商原文；消息来自标准固定表。能力不支持使用`unsupported_capability`，无可用目标保持`upstream_failure`，身份未识别、权限、凭据、覆盖与参数错误保持分别分类。请求关联使用数据请求requestId，不能用另一个传输trace替换。

新能力的API合同测试须覆盖真实HTTP投影与请求关联、安全文案、消费端分类及非法信封拒绝。正确的可选来源失败可进入组合failed项；Schema/控制错误继续阻断消费，不能以宽松解析使验收通过。SDK、预算、分页、源修订与旧冻结不因错误格式整理改变，真实业务/冻结与目标同步仍另行验收。

HTTP测试须挂载`api.middlewares.error_handler.add_error_handlers`：生产全局处理器原样保留标准Data错误detail，不能转成字符串或丢弃关联；平铺Control/通用ErrorResponse/普通字符串错误保持原响应行为。单独router通过不代表生产完整处理链通过。

## 东财拆分失败诊断与时钟边界

`eastmoney_split_diagnostics`只服务原东财拆分读取链。日志事件`eastmoney_split_failure`的阶段区分准入前后、本地证据、映射预检、请求窗口、HTTP、解析、分页、规范化、来源结果/修订、事实投影、标准响应和wire校验。同一异常沿调用栈只记一次；回调或日志设施失败继续抛原业务错误，不执行重试。共享现金传输、Registry供应商分派、原修订和公共事件响应没有变化。

诊断仅保留固定阶段/错误类别、合法HTTP状态、UUID关联、校验错误数量/有限字段名及`availableAfterCutoffCount`；不输出异常消息/堆栈、URL、头、页原文、输入值或凭据。403/503只说明相应层的失败，不能独立证明收费权限。来源在返回前调用同一既有Split validator记录响应错误，Registry仍保留原校验和相关性检查；额外校验没有I/O，也不改变成功返回字段。HTTP/wire保持原安全错误码，内部诊断不透传为新的API字段。

固定截止的`dataAsOf`继续拒绝更晚的`availableAt`；来源仍在实际读取后记录`observedAt`，映射不回填经济事件日期或请求开始时刻。用户已确认显式采集信封的本地实施，Server保存原意图/有限deadline交换与实际完成截止；原固定事件接口/观察时刻和共享Split合同保持。[本地实现与证据](../../thesis-ledger/docs/reviews/2026-10-11-unified-data-capability-runtime-evidence.md#record-k37d-split-clock-implemented-local)只证明合同与局部消费，不代表目标同步或实源成功。`coverage.complete=false`及原完整冻结受阻、原验收标准保持。

原Control GET `/control/routes/capabilities?contractVersion=3&eventAdmissions=v1`返回显式`event-admission-catalog-v1`，包含原catalog与已有事件准入公开快照；默认查询的V3响应形状保持。目录不透明修订加入精确记录身份，因此同ready但版本/范围/证据/期限变化也改变修订。快照只在当前就绪事件中生成，复用既有当前准入读取及公开snapshot，不含审核人、凭据值或原文、不读SDK、不签发。无法核实记录时不填快照，采集端拒绝缺失；不能用ready猜同一scope或记录。前后快照与真实数据响应须一致，真实权限、覆盖及普通冻结仍另行验收。

2026-10-13原目标已正式同步该视图及采集契约，159516.SZ/2026-03-30～07-10现有准入版本1/原936字节映射保持。原appliedAt误比较最小修正/28项验证及正式同步复用；新增明确一次预算后，原Server事件HTTP200/observed返回两条ratio=2事实，真实版本化信封/实际完成截止及离线消费/0网络通过，CAS恢复117，原主备/配置保持。K37d-live只收口此正向层，coverage=false/普通完整冻结/Runner及父项仍独立受阻；该精确成功不推导完整窗口、其他权益或旧503内因，也不续签/保存凭据、重跑H09或继续相同实读。当前状态与原验收边界以[原Task](../../thesis-ledger/docs/tasks/2026-10-09-unified-data-capability-adapter.md)及[运行证据](../../thesis-ledger/docs/reviews/2026-10-11-unified-data-capability-runtime-evidence.md#record-k37d-split-clock-live-consumed)为准，不维护独立进度。

当前 H07–H12 分层状态在2026-10-13已核对：事件精确诊断由来源 EventBinding/probe_read 拥有，RQ/HiThink/Tushare/AK的代码、合同、正式目标及拒绝层均完成，缺账号不阻塞这些实现项。目标179个相关DSA模块及Server/Worker编译DSA与市场Schema输入匹配，无需重复同步或重跑未变验证。原路由UI五项真实保存/消费、状态关闭零数据/其他风险仍可消费和原16路线CAS恢复至实时120已通过；H08缓存及H09新冻结/断网重放沿原已过证据。

当前普通HiThink/东财现金及登记资格v2已完成，原状态/覆盖未知和有限具体事件核对继续披露，旧严格入口仅兼容。RQ无账号、Tusharefund_div此前40203拒绝，实现与真实成功分层记录且不重验未变化条件。TickFlow免费日K读取/有限口径核对/真实研究及断网重放已过，最终正式update.sh dsa镜像固化及源哈希/绑定/新旧重放复验完成。当前授权/责任只由[原Task§9.5](../../thesis-ledger/docs/tasks/2026-10-09-unified-data-capability-adapter.md#95-当前未完成执行包与阻塞)和[TF-F证据](../../thesis-ledger/docs/reviews/2026-10-11-unified-data-capability-runtime-evidence.md#record-tickflow-free-history)维护，不建设逐窗口审批或另一套完成状态。
