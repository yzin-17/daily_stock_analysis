# 标的目录来源与验收证据

## 2026-10-02 目录恢复

历史失败预算保留。本轮授权完成目录恢复，先用有界诊断定位请求合同：原 `op=dy` 最小请求只返回331字节；安装版 AKShare 1.18.94 的开放基金排行使用 `op=ph` 和明确起止日期。Reader 修复为整个读取固定上海日期窗口，仍严格分页、总量、重复身份、HTTPS、禁止重定向、8 MiB 单页上限和45秒总预算。真实完整读取20481条、21页、33.41秒，指纹 `b61ef6399e36348cf86f7746cf6647bd5e23abda7eeee153e13fe680c8787ab7`。这是当前开放基金排行集合，不证明历史目录或ETF分类。

东财 ETF `push2delay` 在直连与宿主代理下均失败，现有Efinance股票端点独立失败。AKShare Catalog 的ETF集合显式选择新浪官方基金中心 `etf_hq_fund` 节点；实际列表接口与安装版 `fund_etf_category_sina` 一致。新增 `sina_etf_catalog_reader.py`，20秒总期限、单响应2 MiB上限；同节点普通计数→最多5000行全列表→普通计数，严格匹配前后总量和逐行原生 `sh/sz` 场所及六位代码，重复/错身份/空名称/计数变化/截断均整体拒绝。普通计数1693与列表1693一致；`getHQNodeStockCountSimple` 返回1694，属于不同计数合同，不作为该列表完整性依据。只解析固定JSONP包装与精确来源注释，不执行JavaScript。

真实ETF读取1693条，包含 `sz159516`，指纹 `55c1b9a308af41d62f017be89df844c38fe1cce65edfa4360811ba70178cb2dc`。AKShare三集合完整Loader实读35280条：股票5572、ETF1693、基金28015，13.92秒。定向88项通过；新Reader/测试限定flake8与官方syntax/critical门禁通过。这些是本地/宿主来源证据，目标Job与连续来源验收由主仓第一优先级证据记录。Efinance股票失败继续保持来源级失败，不发布部分股票集合。

来源：安装版 `akshare/fund/fund_rank_em.py`、`akshare/fund/fund_etf_sina.py`，以及[新浪官方基金中心](https://vip.stock.finance.sina.com.cn/fund_center/index.html#jjhqetf)。目录来源选择不修改行情路由或RouteAdmission。

## R01.5 候选分页协议只读观察

已新增 `eastmoney_fund_catalog_reader.py`：HTTPS 精确分页、单页响应上限 8 MiB、默认每页 1,000 行、最多 50 页、总预算 45 秒；逐页核对总量，跨页重复代码或计数变化整体拒绝，保留每页内容哈希及整体指纹，不返回部分结果。生产消费者仍须置于现有 Catalog 进程硬期限内。解析/读取共 **19 项通过**，关键 flake8 通过，日志 `/private/tmp/goal-fund-catalog-reader-20260927.log`。

完整读取真实探针使用宿主子进程外层 55 秒硬期限、不写缓存/目录数据库/准入。首次以及两次重试均为 `ReadTimeout`，无完整结果；已耗尽本次新读取器的重试预算，跳过完整成功验收。不能把此前五行响应成功提升为完整分页成功，也没有放宽覆盖断言。读取器尚未接入 Catalog；ETF/开放式基金分类依据及消费者接线继续开放。

已实现 `eastmoney_fund_catalog_page.py`：仅解析受限 `var rankData` 数据声明，不执行 JavaScript；校验请求页码/页大小、总记录与总页数关系、页内精确行数、代码身份唯一性，并拒绝重复/未知字段、脚本表达式和附加脚本。13 项定向测试及关键 flake8 通过，日志 `/private/tmp/goal-fund-catalog-parser-20260927.log`。解析器尚未接入逐页读取或 Catalog 消费者，未同步目标，R01.5 保持开放。

2026-09-27 从宿主以 HTTPS 请求 `fund.eastmoney.com/data/rankhandler.aspx`，沿用已安装 Efinance `get_fund_codes` 的查询维度，但限制 `pi=1,pn=5`；连接/读取超时分别为 5/15 秒。首次 ReadTimeout，第一次重试 HTTP 200，响应 879 字节，SHA-256 为 `6cef52266e0feb291e8d2a890149ca08225a771aa65095a7354ca8c7a223b150`，未执行第二次重试。

观察到字段 `datas/allRecords/pageIndex/pageNum/allPages/allNum` 及分类计数，说明可进一步建立分页校验合同。此次仅观察响应结构，尚未解释全部计数语义、遍历后续页或证明目录完整性；没有创建 Catalog Job、更新缓存或写入准入，也不替代此前已耗尽预算的 Efinance 完整 loader 验收。下一步应固定安全解析与页码/总数/重复身份/变化响应拒绝规则，再接入现有消费者。

归属 M3 R01.1–R01.5；完整登记表仍以 `thesis-ledger-source-capabilities.md` 为准。本文件记录实际实现证据，不另建支持矩阵。

## AKShare 1.18.94 调用链

本地安装源码核验：`stock_info_a_code_name()` 聚合上交所主板 A 股、深交所 A 股、上交所科创板及北交所列表，最终返回 `code/name`。该股票目录不是 EastMoney 来源；SDK 包装名称不能当作实际上游身份。

| 函数 | 实际 endpoint | 函数源码 SHA-256 |
| --- | --- | --- |
| stock_info_sh_name_code | `https://query.sse.com.cn/sseQuery/commonQuery.do` | `8dcb3d64464979607ff8ce2c679c01167443189042715d3f1274236941aad9a6` |
| stock_info_sz_name_code | `https://www.szse.cn/api/report/ShowReport` | `13fa7010071a41bba1ecf2fa958d3d2d60039dc71a86357cffe57eb3d3659004` |
| stock_info_bj_name_code | `https://www.bse.cn/nqxxController/nqxxCnzq.do` | `33484d91d1826f278806403ddba796e0fac837625afb90e37eda607344a16e4b` |
| fund_etf_spot_em | `https://push2delay.eastmoney.com/api/qt/clist/get` | `89c92fba0643b792c5d209465976b0c54a69f1553d656bb7b1cf7f4614a849b3` |
| fund_name_em | `https://fund.eastmoney.com/js/fundcode_search.js` | `845cd50ae986f20072bda3536ce59edc65e190b77abbe8870c73f7d2c8485e22` |

摘要算法为 `inspect.getsource()` 返回 UTF-8 文本的 SHA-256，表明本次检查的函数版本；不能单独证明远端响应或覆盖。上交所和北交所 SDK 请求没有独立传输超时，但 Catalog 外层子进程提供硬时间预算并终止超时进程。北交所实现按响应总页数分页；ETF 实现调用 `fetch_paginated_data`；基金目录读取静态 JS。分页/全量响应仍须真实验证。

## 已完成本地与目标源码检查

目录股票显式 STOCK、920→BJ、无效输入拒绝、同源重复冲突整体拒绝，以及跨来源既有优先级，相关 29 项测试通过。目标当前源码已核验 920→BJ 和 NaN 名称拒绝。当前目录不能证明历史上市状态或自动推导新旧代码对应。

## 真实探针预算

只读目标容器中的 `stock_info_a_code_name()`，外层 60 秒、每次调用 `max_attempts=1`，不创建目录刷新作业或写入准入。首次进程 66995 返回 `catalog_provider_unavailable`；第一次重试进程 14142 成功，未执行第二次重试。

目标实际 AKShare 为 1.18.97，说明完整镜像重建后宽范围依赖发生了版本变化。三个交易所函数源码摘要与上表宿主 1.18.94 一致；目标聚合函数摘要为 `c74b7eff892c34e415319e5c41d2e3da6895a09d432dc5187e06a7d7ff4f23d7`。

成功响应标准化后共 5,569 条，SH 2,320、SZ 2,902、BJ 347；北交所 SDK 完成 18 页遍历。以代码排序、字段排序、紧凑 UTF-8 JSON 编码后的内容 SHA-256 为 `cb0bff7dc3c8e149d0e385527260e876d72bb1a00d8238776361790e4e75501a`。这是当前目录快照的只读观察，不证明历史目录、连续可用性或未来响应不变。

R01.1 尚缺完整消费者刷新验收：现有 `_akshare_catalog` 同次还读取 ETF 和基金列表，不能由股票函数成功推定整个 loader 或目录作业成功。未创建目标 Catalog Job，未修改准入。

完整消费者追加探针：目标直接执行 `build_catalog({akshare: _akshare_catalog})`，每轮外层 60 秒且 `max_attempts=1`，不写目录数据库。首次进程 9547、第一次重试 17139、第二次重试 22152 均返回 `catalog_all_providers_unavailable`，内部记录为 Provider 请求失败。已达到用户允许的两次重试，本轮跳过完整消费者真实验收，不能计为通过。该预算单独针对三资产完整读取，不与前述单股票探针混计。原始 SDK 可有分页及内部重试，外层次数不是 HTTP 请求数量。

另修复子进程错误协议丢失应用 CatalogBuildError 的稳定 code/retryable：无效响应的不可重试标记现可跨进程保留，通用 SDK 异常仍统一不可用；不传输原始异常消息。此源码修复已通过官方同步入口部署并核对文件摘要，不重置上述真实探针重试预算。

验证：真实 spawn 子进程测试确认 max_attempts=3 时不可重试错误只调用一次；目录相关共 30 项通过，关键错误 flake8 通过。

专题无对应英文版。

## BaoStock 名称消费选择

R01.6 选择现有 `DataFetcherManager.get_stock_name`，由 stock_service、研究工具及流水线消费。BaoStock 入口为 `query_stock_basic(code=精确代码)`，仅采用 code/code_name 生成当前名称，不能推定历史有效期。实现新增唯一响应、代码匹配、非空文本名称和迭代终态校验，错误不写缓存且会话仍登出；9 项身份回归加既有预取 12 项通过，关键错误 flake8 通过。

2026-09-27 目标验证：官方 `./scripts/sync-code.sh dsa` 成功，目标健康，镜像仍为 `sha256:d4a11dfbf07eba886f9b385aa028dbedde0b8f8d997e97a82f9d0163834192d0`。同步日志 `/private/tmp/goal-baostock-catalog-sync-20260927.log`；更新位于容器可写层。宿主与目标 `data_provider/baostock_fetcher.py` SHA-256 均为 `3e02e4cf66427decc84a7ed8da9159f87aea47ac29fc2c58975979e558d3fa2b`，`src/services/thesis_ledger_catalog.py` 均为 `73bad612984789f701648dbc6122bedb2a80dadea89c067939bd7328bd1c7fec`。

只读探针 `/private/tmp/goal-baostock-probe-20260927.py` 在目标 BaoStock 0.9.4 中直接调用实际 Fetcher，单次硬预算 50 秒。首次返回 `600519 → 贵州茅台`，并确认进程内缓存匹配，无需重试。未创建目录作业、修改准入或写业务数据库。该证据覆盖单标的当前名称适配，不代替 Manager 全链路、其他市场、历史名称及挂牌状态的支持范围验收，R01.7 的范围门禁继续开放。

R01.8 的列表入口只被 `prefetch_stock_names(use_bulk=True)` 用于名称预取；get_stock_list 当前丢弃上市/退市日期、类型和状态字段。这个消费关系不足以实现历史证券列表，R01.8/R01.9 的状态合同继续开放。

批量名称适配的前置修复合同：读取完成且最终状态成功后，才允许整体发布 `code/name` 与更新缓存。代码必须是市场前缀加六位 ASCII 数字，名称必须是非空文本；重复源身份与名称相同可去重，身份或名称冲突使整批失败。尤其去掉市场前缀后发生冲突时不得覆盖已有名称，也不得从代码推断证券类型。失败保留调用前缓存，成功沿用现有两列消费者契约。此修复不提供历史列表或挂牌状态。

本地实现验证：解析职责位于 `data_provider/baostock_names.py`，Fetcher 在验证返回后一次性更新名称缓存。新增完整成功/同源去重，以及晚到错误、同码异名、跨市场同码、坏代码、空名称、缺字段、重复字段与空集等回归；名称及预取相关共 39 项通过，关键错误 flake8 通过。官方同步成功且目标健康，日志为 `/private/tmp/goal-baostock-bulk-sync-20260927.log`；镜像未变，源码位于可写层。宿主与目标新 helper 的 SHA-256 均为 `becdcc000faf85bce5b3e990f7d9c1b80aea1f3aea45de589a67eed6e1a3eda0`，Fetcher 为 `5d8ba1697cd9cb8f1bbdfe814151aa0617fd61d9ef21e3736da4cfb20246040b`，替代前次源码摘要。批量真实来源尚待验证；不得把响应冲突拒绝当作来源完整性验收。

Manager 消费追加验证：`tests/test_baostock_name_identity.py` 增加三个代码别名的标准化/缓存复用，以及错标的响应失败后可恢复、两层缓存不污染的回归；连同既有预取共 25 项通过，关键错误 flake8 通过。目标受控探针 `/private/tmp/goal-baostock-manager-probe-20260927.py` 通过构造器只注入实际 BaoStock Fetcher，临时屏蔽进程内静态名称与索引映射，使用 `allow_realtime=False`。首次实际读取 `SH600519` 返回贵州茅台，随后 `600519` 命中 Manager 缓存，实际 Fetcher 仅调用一次。外层 50 秒硬预算，首次成功，无重试、无业务写入。此证据覆盖真实来源经过 Manager 的名称链路；未验证默认多源优先级、所有 CN STOCK 或历史身份，因此支持范围门禁仍开放。本轮只新增测试及证据，生产源码无需再次同步。

## BaoStock 批量真实探针结果

2026-09-27 通过 `/private/tmp/goal-baostock-bulk-probe-20260927.py` 在目标容器执行真实 `get_stock_list`，单轮硬预算 50 秒，不写业务数据。首次及两次重试均读到 8,981 行并返回“名称响应身份冲突”，三次均未发布列表且进程内缓存为零。探针仅包装验证函数记录行数和固定验证错误，不改变实际验证结果。已用尽该批量探针的重试预算，本轮跳过其真实成功验收，不再次请求同一路径。

这证明现有只保留六位代码的批量名称合同不能无条件承载整份来源响应；现有证据尚未细分同码异名或跨市场同码，不能猜测具体冲突条目。需另行明确带市场/证券类型的列表消费契约后再处理，不放宽验证以取得成功，也不把此结果当作历史证券列表已实现。

## Efinance 0.5.9 基金目录缺口

股票入口核验：目标 `stock.get_realtime_quotes(fs=None)` 默认使用 `FS_DICT["stock"]`，股票过滤条件为 `m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048`；其函数源码 SHA-256 为 `b1e8f10e903bd0c5f8c50c2eb0be739e8ed200b71b69bf091955d1bb58a5e293`。下层 `get_realtime_quotes_by_fs` 实际请求 `http://push2.eastmoney.com/api/qt/clist/get`，首请求获取 total 与每页数量后计算页数并并发读取，因此仍属于 EastMoney 来源。源码读取没有触发真实行情请求，分页源码不能代替真实完整响应验收。

目录改为所有入口必须显式提供 `instrument_type`，Efinance 默认股票入口固定 STOCK，移除目录代码前缀推断。北交所/沪深入口回归及缺少类型拒绝已覆盖，目录相关 32 项通过，关键错误 flake8 通过。真实 Efinance 目录与基金分类门禁继续开放；当前修复不使缺失的基金 SDK 入口变成已支持。

显式类型修复已由官方代码同步完成，日志 `/private/tmp/goal-catalog-explicit-type-sync-20260927.log`，目标健康且镜像未变。目录文件宿主与目标 SHA-256 一致，为 `816c76a1f7fb656e3c462c3ddd83a0c4c9a43321238e01360e5d8adb2a00826c`，替代此前目录源码摘要；更新仍在容器可写层。本次没有重新运行已耗尽预算的 AKShare 完整目录探针。

真实 Efinance 探针：`/private/tmp/goal-efinance-catalog-probe-20260927.py` 在目标直接调用实际 loader，单轮硬预算 50 秒。首次及两次重试均返回 `JSONDecodeError`，未获得有效目录，未写业务数据库或准入。已用尽本轮预算并跳过真实成功验收。异常类型不能单独判断是上游正文、代理还是 SDK 解析原因，因此不据此改变权限或来源分类；32 项离线测试仍不替代该门禁。

目标安装包没有 `fund.get_realtime_quotes`，现有 loader 的条件分支会跳过基金目录，不能据股票目录成功宣称基金能力已实现。包内提供 `fund.get_fund_codes(ft=None)`，返回基金代码/基金简称，实际调用 `http://fund.eastmoney.com/data/rankhandler.aspx`，固定 `pi=1,pn=50000`，通过正则抽取响应且未验证总页数，内部装饰器最多尝试 3 次。

该函数只说明候选入口存在；固定大页和正则非空不证明完整性，不能直接签发完整目录。既有有界 Reader 和下述 Catalog 消费接线不把 Efinance 包装视为 EastMoney 的独立来源。

### 2026-09-28 开放基金分类合同与 Catalog 消费接线

分类前提已收敛到可审计的当前合同：[EastMoney 基金排行页](https://fund.eastmoney.com/data/fundranking.html)将“开放基金排行”与“场内交易基金排行”分开展示；安装版 AKShare 1.18.94 的 `fund_open_fund_rank_em` 同样把该页面/接口声明为“开放基金排行”，并在“全部”类型下显式使用 `dt=kf,ft=all`。因此本接缝只在这组参数和来源合同保持成立时，将完整分页结果投影为 `MUTUAL_FUND / OF`；不按六位代码、名称、`etf_count` 或其他聚合计数猜 ETF。场内 ETF 继续由独立目录入口和来源证据负责。

`eastmoney_fund_catalog_reader._fetch_page` 现在显式发送 `ft=all`。目标 Efinance 0.5.9 没有 `fund.get_realtime_quotes` 时，`_efinance_catalog` 使用该有界 Reader；若未来/其他版本存在原生基金目录方法则仍优先原生方法。Reader 的 ValueError/协议失败映射为不可重试 `catalog_provider_invalid_response`，总预算超时映射为可重试 `catalog_provider_timeout`；消费者再次检查冻结 rows 的 tuple 形状、六位 ASCII 代码、非空名称和代码唯一性。

该 fallback 仍运行在现有 Catalog 可终止 spawn Provider 进程内，因此股票入口与开放基金 fallback 共同受父进程硬期限约束。基金分页失败会使整个 Efinance 来源原子失败，已经取得的股票行不会作为同来源部分目录发布；跨 Provider 既有优先级不变。

红例先得到 5 failed / 1 passed：缺 `ft=all` 两项、缺 fallback/错误映射三项；实现后同 6 项通过。随后 parser、分页 Reader、HTTP 边界、Catalog source/build/job 五文件合计 **68 passed**；改动 Python 的 E9/F63/F7/F82、`py_compile` 与 tracked diff check 通过。测试均为离线 fixture，没有再次访问 EastMoney。

真实完整读取的首次及两次既有重试仍均为 `ReadTimeout`，该预算不因分类合同或消费接线完成而重置。因此这里只完成本地分类/消费实现；真实完整覆盖、连续可用性、历史目录资格和目标 Catalog 正向刷新仍未通过，R01.5 父门禁保持开放。
