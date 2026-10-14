# ThesisLedger 公共收费本地主源

## 备用规则包

`official-rules / cn-stock-auction-fees-archive` 读取独立文件 `data_provider/public_fees/cn-stock-auction-archive-2023-08.json`，覆盖 2023-08-22～29。主包缺失或请求含 08-22 时仍可单独读取备用；两包都不覆盖 08-21。目录分别检查所选文件，不因为另一个文件存在而标记就绪。统一路由只读取明确 target，主仓 Snapshot 准备负责顺序尝试，两包内容不拼接。

备用印花税变化依据[上海税务局第39号公告](https://shanghai.chinatax.gov.cn/zcfw/zcfgk/yhs/202308/t468451.html)，文书成文日为 08-27，该页面发布日为 08-28，包保留后者，不作为严格 PIT 时钟。经手费来自[国务院网站新华社转载](https://english.www.gov.cn/news/202308/18/content_WS64df6b79c6d0868f4e8deaa6.html)，过户费来自[武汉金融局公开转载](https://jrj.wuhan.gov.cn/ynzx_57/xwzx/202204/t20220429_1964160.shtml)。印花税法和发改价格规〔2018〕917号与主包共用政策依据。这里只存必要费率事实和引用，不复制文章正文；备用包是本地读取容错，不表示独立政策事实或官网在线容灾。

主仓在准备 Snapshot 时冻结实际选中包及回退原因；Runner 只读已冻结内容。两包均失败且已有明确费用模型时，沿用模型并披露原因。首个主源的真实股票运行已由主仓 P3 目标证据验收；备用目标验收由主仓 P4.3 单独记录，本文下方旧阶段统计是历史本地证据。

本模块是主备数据源方案首批的收费适配器，使用生产数据包 `data_provider/public_fees/cn-stock-auction-2023-08.json`。市场范围为上交所 A 股竞价，覆盖 2023-08-23～29，包含印花税、经手费、监管费、过户费及 08-28 费率切换。数据包属于来源适配输入，放在可跟踪并随 Provider 打包的源码目录。扩展起点沿用变化前的已读规则，允许同一真实回测分别产生变化前后卖出；已有快照内容不随新包改变。

调用 `src.services.thesis_ledger_public_fees.OfficialPublicFeeProvider.read(request)`；请求复用 V3 `kind=data` RouteKey，能力为 `HISTORICAL_PUBLIC_FEES`，选中目标为 `official-rules / cn-stock-auction-fees`，包含目标顺序、标的及日期。日期请求终点包含当日，响应分段采用左闭右开区间。

本地读取不联网或自行切源。结果保留来源 URL、文号/标题、发布日期、政府新闻转载性质、内容版本摘要与当前取得时刻。数据包不是测试 fixture；共享 wire fixture 从本模块读取真实生产包生成，仅用于跨语言合同验证。

该来源只返回公共费率。最低额、金额取整、扣收分组、佣金及规费包含关系由已明确选择的执行模型提供；响应标记 `calculationSettings=executionModel`。未证明历史首次可见时钟时标记 `historicalAvailability=notVerified`，不宣称严格 PIT 或复原券商实际扣款。

控制目录已登记内置 Provider `official-rules` 和精确来源 `cn-stock-auction-fees`，无需凭据或人工准入记录。现有策略可以将其选为收费主源；统一执行入口只使用当前生效配置中的精确 target，读取后仍检查策略修订，防止晚到结果跨越停用或配置变更。

正式读取入口为 `POST /api/v3/thesis-ledger/market/public-fees`，沿用 Data Token 认证和 V3 请求关联错误。请求包含 `contractVersion`、`consumer`、`requestId`、`routeKey`、`routeTarget`、`symbol` 和 `range`，字段定义以主仓 `market-public-fees-v3.ts` 为准。覆盖不足返回 `insufficient_coverage`，标的不适用返回 `not_covered`；未配置或停用的目标不会被直接读取。

阶段状态：来源读取、控制目录、统一请求路由、HTTP 和主仓配置中文展示已完成本地验证。主仓 P3.3 已接入独立收费 Snapshot 产物及离线 Runner/基准费用消费，创建时按执行整窗读取，重放不再请求来源；真实价格源与目标运行验收仍由 P3.4 承担。没有新增配置项、依赖或数据库结构，也未更新目标容器。

已执行来源定向 13 项、HTTP/control 定向 9 项和既有路由回归；DSA `scripts/ci_gate.sh all` 通过 7,813 项测试与 626 个子测试。HTTP 验证使用真实生产数据包与临时 SQLite，包括认证、精确目标、策略修订、窗口错误及无人工准入行；不是目标服务或回测运行验收。
