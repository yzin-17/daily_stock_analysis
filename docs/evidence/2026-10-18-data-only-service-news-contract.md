# 新闻 N1 合同实施证据

> 主题日期：2026-10-18；执行日期：2026-10-19（Asia/Shanghai）。状态由[原 Task](../archive/tasks/2026-10-18-data-only-service.md)维护；本页不是新的实施计划。

## 实际交付

- `src/services/thesis_ledger_news_contract_v1.py`：严格请求/条目/覆盖/错误/响应；公开标的与时间窗口、真实空结果、不完整及零尝试拒绝保持区分。未知发布时间保持 null，来源可见与抓取时间独立，所有时间要求显式时区。
- `src/services/thesis_ledger_news_identity.py`：规范市场标识、URL 身份、稳定条目及转载证据分组；原始 URL 与出处保留，不执行源文。分组不把转载数变成独立证据数。
- `src/services/thesis_ledger_news_capability.py`：沿用现有精确 `CapabilityContract/QueryBinding`，核对请求标识、市场/类别、窗口、条数及实际目标。DTO 独立导入不加载旧全局配置、分析或模型 SDK。
- `contracts/thesis-ledger/news-search-v1.samples.json`：12 组固定受控样例。ThesisLedger T1 按同版样例接续，不另外定义同义合同。

没有注册真实来源、开放运行路由、改变主备、读取凭据、调用搜索或模型、删除原生产品、迁移存储或更新 Docker。N1 是合同交付，不是 N3/DG3 的新闻取数与真实消费验收。

## 执行检查

1. `.venv/bin/python -m pytest -q tests/test_thesis_ledger_news_contract_v1.py`：初版 34 项通过；补来源身份歧义样例后为 35 项。
2. `.venv/bin/python -m pytest -q tests/test_thesis_ledger_news_contract_v1.py tests/test_thesis_ledger_capability_registry.py`：最终 279 项通过。包含共享样例、私有材料/无界请求拒绝、未知/失败/截断不能升级 available、伪造身份/错误来源/非法 URL/无时区拒绝、独立导入与现有 Registry 关联校验。
3. `.venv/bin/python -m py_compile src/services/thesis_ledger_news_contract_v1.py src/services/thesis_ledger_news_identity.py src/services/thesis_ledger_news_capability.py tests/test_thesis_ledger_news_contract_v1.py`：通过。
4. 在仓库目录将 `.venv/bin` 加入本次进程 PATH，执行 `bash scripts/ci_gate.sh syntax`、`bash scripts/ci_gate.sh flake8`、`bash scripts/ci_gate.sh deterministic`：全部通过；确定性入口只检查代码识别及符号转换，没有实源请求。

pytest 的既有 Starlette/httpx/AnyIO 弃用提示及已有测试类收集提示保留；没有升级依赖。全仓离线 pytest、独立 ASGI、无 LLM 的完整数据运行、Docker、实源与恢复均未运行，不能由此计作通过。

## 保全、自审与下一叶

仅新增上述数据合同、样例、专用测试，更新所属 Spec/Task/CHANGELOG。原 38 条 DSA 未提交修改保留，未触碰既有配置、FX、公告/OAuth/控制存储、产品调用方或部署文件。没有 commit、push、tag、PR。

自审确认：成功空列表要求实际成功检索；not_admitted 要求零尝试；unknown 不补抓取时间；partial 保留具体原因；两个转载保留原始出处但只计一组独立证据；不可信工具指令保留为普通摘录。真实来源是否返回有效证据由后续 N2/N3、消费资格由 T2/T5/DG3 独立验证。

DSA 下一叶是 D0，N2 等待 D2c；ThesisLedger 可先执行 T1。现有 D0 删除闭包尚未完成，不得据合同落地删除产品目录或共享依赖。英文 README/指南未修改：本叶没有改变既有启动、公开 API 或用户配置入口，中文专题 Spec 和版本化样例持有新合同。
## N2a：原始记录与覆盖适配（2026-10-19）

新增内部 `RawNewsRecord/RawNewsRead` 与纯结果适配；公开 `news-search-v1`、12 组冻结样例及其内容指纹保持。成功空列表需要真实尝试；失败/零尝试不能得到 available。未知、无时区或不可核验发布时间保持 null，抓取时间不回填；闭区间保留两端，未来尚未观测窗口不声称完整。

转载按规范 URL、已知原始出处或内容指纹保守合并，保留不同渠道 URL。混合已知/未知原始出处时选择真正声明原出处的记录作为主项，不向未知项补造出处；相同来源 URL 的冲突内容保留不完整原因。条数/游标/引用上限、部分失败和私有凭据拒绝均显式进入覆盖/错误。源文指令仍为原始 excerpt，不执行、不生成模型摘要。调用方的标的核验和来源许可仍由 N2c/N3 负责。

`.venv/bin/python -m pytest -q tests/test_thesis_ledger_news_results.py tests/test_thesis_ledger_news_contract_v1.py`：49 项通过，含新增 14 项，2 条既有依赖弃用提示。新增两模块和测试完整 flake8 为 0。该阶段没有运行 Provider 或模型；N2b 的执行证据见下节。

## N2b：有预算的来源执行（2026-10-19）

新增 `thesis_ledger_news_sources.py`、`thesis_ledger_news_transport.py` 和 `thesis_ledger_news_execution.py`，复用 Tavily、SerpAPI、Bocha、Anspire、MiniMax、Brave、SearXNG 的现有检索实现；每次只装配选定来源。SearXNG 只接受显式实例，禁止公共实例发现；其他来源限制在其声明的 HTTPS 上游域名。Bocha 原有 `summary: true` 会要求上游生成摘要，现改为 false，专用回归固定该边界。

实际 HTTP 传输前记录 attempt，SDK/启动/配置失败及未开始执行不虚增计数。429 是一次限流响应，原有瞬态连接重试的三次 HTTP 各计一次。响应缺少声明列表时判为失败，坏行与引擎部分失败保留原因；显式原始发布时间、原出处、来源 ID 与来源可用时间保留。成功空列表与坏响应分开，不使用模型生成内容。

取数使用最多四个隔离进程；总请求预算中预留两秒释放执行资源，余量不足时零尝试返回超时。到期会 terminate/kill、join、关闭 IPC，并在真实执行结束后释放并发槽；不以线程超时冒充执行停止。受控 HTTP 还验证跨 origin 重定向在第二次请求前拒绝、超时进程回收、启动失败和并发上限；未调用外部搜索商、模型或通知服务。

验证：`tests/test_thesis_ledger_news_execution.py` 15 项、`tests/test_thesis_ledger_news_transport.py` 16 项通过；连同 `tests/*news*py` 的固定合同、结果适配和既有新闻回归，共 197 项通过。新增新闻模块及两测试文件完整 flake8 为 0；既有大文件 `src/search_service.py` 的语法/名称检查为 0，完整样式检查仍有既有空白/缩进债务，未进行无关格式化。初始测试误把 429 预期为三次重试，核对原 Adapter 后修正，并增加实际断连三次重试的独立证明。

该批次仅证明 N2b；N2c 的公开身份/配置组合和 N3 的正式路由证据见下节。没有启用生产来源，没有目标容器更新，不能将本地传输证明视为实源或部署验收。

## N2c：公开身份与配置组合（2026-10-19）

新增 `thesis_ledger_news_instruments.py`、`thesis_ledger_news_service.py`，组合已选定来源的身份、独立配置、剩余预算及 N2a 结果。身份来自现有数据资产或 checksum 有效的已发布目录；目录存在同代码条目时优先使用其市场与资产类型。读取目录使用 SQLite `mode=ro`，不创建库、不初始化旧产品表或生成 fixture。现有索引的 US 代码按固定合同加 `.US`；不从显示名、裸代码或其他市场推断身份。无法核验的 ETF/指数等保持不可用，不把股票索引的名称当成完整资产目录。

配置加载失败返回 `configuration_failure`，正常空凭据返回 `missing_credentials`，公开身份冲突/未核验返回 `ambiguous_instrument`；这些路径均为零上游尝试。显式控制凭据不回退到环境值，秘密字段不进入 settings repr 或错误。只将核验后的公开代码、名称、市场和类型用于查询，不接收私人材料。配置/身份读取用去的时间从请求预算扣除；超过一年的起点显式标为覆盖不完整。

18 项组合测试通过，包含实际数据资产上的 CN/HK/US 身份、已发布目录 ETF/场外基金、数据库字节保持、checksum 损坏与冲突、同名跨市场、七种显式凭据、失败脱敏以及真实本地 HTTP → 既有 SearXNG → 固定新闻 DTO。新闻五个专用测试文件合计 98 项通过；新增新闻源码及专用测试完整 flake8 为 0。两条既有依赖弃用提示保留。没有读取用户业务库、调用外部 Provider、模型或通知。N2 完成；N3 的正式路由/准入与后续 DG3 实源消费仍保持未完成。

## N3a：默认关闭与精确来源（2026-10-19）

`data_provider/thesis_ledger_sources/news.py` 声明七种既有搜索来源的精确 QueryBinding，当前支持 CN 股票/ETF/指数/场外基金及 HK/US 股票；目录不能核验的具体标的仍由 N2c 拒绝。凭据沿用 Control 只写加密合同，以 `search` 方法保存 JSON 字符串列表，校验空列表及无效实例地址。声明不导入搜索 SDK、不执行请求。公开凭据修订仅含来源及控制版本；内部准入 HMAC 不进入新闻响应。

SourceDefinition 增加默认值为 true 的 `default_enabled`，保持原有来源行为；新闻声明为 false。Control 的 Registry 显示与路由资格复用同一默认状态，环境中有完整凭据也不能自动开启新闻来源。新闻路由须明确 `enabled`，绑定保留 `admission_required=true`；显式开启仍需要有效精确准入。没有向用户控制库写入配置、策略或准入。

`tests/test_thesis_ledger_news_sources.py` 初版新增 10 项；与 feature policy、Control、Registry 回归合计 370 项通过。此前 Registry 与新闻固定合同另有 363 项通过，包含新增绑定自动纳入的参数化检查；两批存在重叠，不相加。新来源/feature policy/专用测试完整 lint 为 0；Registry 完整样式检查发现两处既有续行缩进债务，未扩大格式化范围。一个旧 Control 测试的局部配置替身改为完整 DataServiceConfig，以保留其“不合并环境凭据”断言。该批次仅证明 N3a；后续正式 HTTP 见下节，目标部署仍独立验收。

## N3b/c：正式执行、缓存与独立 HTTP（2026-10-19）

新增新闻 query/gateway/cache 与认证 POST 模块，挂载到已有 V3 数据 router；请求仍经当前 Runtime 的策略选择、精确来源准入、持久化健康状态与返回前检查。需独立准入的 QueryBinding 现在按其声明核对凭据与来源修订，未放宽价格或其他能力的门槛。新闻来源增加内部 HMAC 载荷和策略准入守卫；环境内容发生非空变化时，策略显示与执行同时失效。仅保存首次设置也保持默认关闭，新增第 11 项来源测试证明这一点，旧来源默认行为保留。

每次备源执行只使用原总预算的剩余量；主失败和备源实际请求均保留来源及计数，备源成功为 partial。测试在传输末端仅将 Brave 的真实 Adapter 请求改送本地受控 HTTP，保留声明来源检查、JSON 解析和请求事件；两次实际请求得到两次计数。五秒预算中主源耗时后，备源零请求，真实取数子进程结束。没有外部搜索商或模型调用。

重放缓存按完整请求（含业务 requestId）、公开身份、精确目标、策略、Adapter/来源、内部凭据指纹与来源控制版本隔离，限 64 项、1 MiB、60 秒。只缓存 available，保留原始 fetchedAt/attempts；同一业务请求的重放不伪造新 HTTP。关闭、撤销、配置/环境凭据变化、请求/窗口/条数变化和过期均有证据。中途撤销会丢弃条目，保留实际请求次数并返回 upstream_failure；未开始请求的 not_admitted 保持零尝试。首次无策略、已明确关闭、已开启后环境凭据丢失分别正确返回。

独立进程测试使用实际 `api.data_app.create_app()`、真实生命周期与 Uvicorn TCP，控制库和 ENV_FILE 全部位于临时目录，远程索引刷新关闭。经认证 POST 处理成功、真实空、引擎部分失败、坏响应、未知时间、转载、不可信源文和分页截断八种实际本地 HTTP 输入；另验 401、私有字段 422、缓存后撤销零尝试。未知发布时间仍为 null；转载保留两条来源 URL 但只计一组证据；源文保持字符串。退出时生命周期标志已释放、活动取数子进程为零，未加载旧 Config、原生 pipeline、通知或 OpenAI/LiteLLM/Anthropic SDK。

验证批次（存在重叠，不相加）：

- 新闻全部十个专用文件一轮 141 项通过；之后补环境非空变更断言与同步准入投影，在最终输入运行 N3 五文件 44 项和受影响配置/准入/凭据/Registry/Control 基线 490 项，共 534 项通过。未变合同/结果/传输测试复用先前通过证据。
- 新增新闻源码、专用测试、来源声明、API 与修订模块的完整 flake8 为 0。
- `bash scripts/ci_gate.sh syntax`、`flake8`、`deterministic` 在最终输入全部通过；保留既有依赖弃用与测试 Client 收集提示。
- 三仓 `git diff --check` 为 0，暂存文件为 0；没有 commit/push、目标 Docker 更新、真实来源准入或模型调用。

N1～N3 的本地新闻生产链完成。此环境仍物理安装 LLM 包，测试证明没有导入/副作用；DG1 必须另用隔离环境完成 D0 全部受影响数据类别，不能由本节代收。DG3 的目标实源与 ThesisLedger 消费也继续未完成。下一主仓门禁 G1 可以使用这些正式数据模块与受控来源推进。

<a id="dg3-source-inventory-20261022"></a>

## DG3：现有新闻来源就绪盘点（2026-10-22）

用户授权先盘点现有新闻来源，发现合法可复用来源后直接推进 DG3。已核对当前来源声明、搜索适配器、配置解析、目标公开配置、宿主机环境与已有服务；本次范围内没有找到可直接用于实源验收的配置。DG3 保持未完成。

| 已有来源 | 所需配置 | 本次目标状态 |
| --- | --- | --- |
| Tavily | `TAVILY_API_KEYS` 或控制面 `apiKeys` | 未配置、未启用，配置版本 0 |
| SerpAPI | `SERPAPI_API_KEYS` 或控制面 `apiKeys` | 未配置、未启用，配置版本 0 |
| Bocha | `BOCHA_API_KEYS` 或控制面 `apiKeys` | 未配置、未启用，配置版本 0 |
| Anspire | `ANSPIRE_API_KEYS` 或控制面 `apiKeys` | 未配置、未启用，配置版本 0 |
| MiniMax 搜索 | `MINIMAX_API_KEYS` 或控制面 `apiKeys` | 未配置、未启用，配置版本 0 |
| Brave Search | `BRAVE_API_KEYS` 或控制面 `apiKeys` | 未配置、未启用，配置版本 0 |
| SearXNG | `SEARXNG_BASE_URLS` 或控制面 `baseUrls`，使用已有专属实例 | 未配置、未启用，配置版本 0 |

### 盘点范围与实际观察

- 七个来源的 `configured=false`、`credentialConfigured=false`、`credentialSource=none`，对应凭据字段也均未配置。当前政策与 DSA 修订均为 166，原 16 条路线中没有 `NEWS_SEARCH`。
- 当前进程环境中，七来源的正式变量及常见单数别名均为空；工作区四个根目录的实际 `.env*` 文件仅发现 infra `.env`，没有新闻配置。四个现有 Shell profile 中也没有对应配置赋值。只记录字段存在性，没有输出或保存凭据值。
- 当前五个 Docker 服务中没有独立新闻/SearXNG 服务；宿主机只读进程名称检查也未发现可识别的现有新闻服务。此结论限定于本次工作区、配置与服务盘点范围。
- 现有三个 AI Provider 的公开名称、类型与地址中，没有 MiniMax 来源候选；未读取或改动其中的凭据，也没有模型调用。
- 当前生产源码的新闻路径复用 `src/search_service.py` 中七个现有适配器；`src/services/thesis_ledger_news_sources.py` 为 SearXNG 显式设置 `use_public_instances=False`。没有已接入的免凭据新闻替代入口，不能以市场数据来源已配置推定新闻也可用。

### 复用、保护与接续

N1～N3 已过的合同、规范化、预算/回收、路由、缓存及受控 HTTP 证据继续复用；DG2a/DG2b/DG2c 的目标镜像、留存与恢复结果由原 Task 持有。本轮只补来源就绪盘点，没有重复测试、构建、部署或发起供应商请求，未将盘点结果计为 DG3 正向消费。

目标健康正常，原政策摘要 `135215323132ea37780e0b9ba7d8a7b21cb7079171216eaffc1b44aa2a9c4d5f` 已登记；Tushare 保持已启用、凭据已配置、版本 3，120 分的既有验收不重做。未变更来源、路线、账户、持仓、草稿或模型设置。私有结构化回执位于同级主仓 `.recovery-artifacts/remaining-p1-2026-10-20/dg3-news-source-inventory-20261022.json`。

恢复条件为一组已有合法可用的新闻 API 凭据，或一个允许使用的现有 SearXNG 专属实例地址。具备后沿用本次授权，通过正式配置入口准备单一精确路线，以现有明确标的和小窗口完成真实新闻 → V3 → Market 读模型、源文/时间/质量显示及拒绝验收；模型继续暂停。没有新增注册、采购、公共实例发现或其他来源任务。

<a id="dg3-free-news-20261022"></a>

## DG3：免费官方来源与真实消费验收（2026-10-22）

用户进一步授权查找免费新闻源，可用时接入并推进 DG3；此前“现有七源没有配置”的盘点属于当时范围，不再作为本叶当前阻塞。现按[配套规格](../../../thesis-ledger/docs/specs/2026-10-22-free-news-dg3.md)完成来源、消费、正式部署与最终复核；研究 G3 的模型和发布条件独立保留。

### 来源调查与精确范围

- GDELT 官方 DOC API 免费开放，但本机两次请求均返回 429，第二次明确要求控制频率。本次不绕过限流、不持续探测。
- NVIDIA [官方 RSS 说明](https://www.nvidia.com/en-us/about-nvidia/rss/)明确免费供个人非商业信息用途；`https://nvidianews.nvidia.com/releases.xml` 主机返回 HTTP 200、131090 字节、20 条近期消息，约 1015 ms。微软新闻订阅虽返回 200，但最新内容停留在 2025 年 5 月，未选用。
- 使用 `nvidia-news / nvidia-newsroom-rss`，仅 `US / STOCK / NEWS_SEARCH` 与已有公开身份 `NVDA.US`。无需 Key、注册或新秘密；默认关闭，实际启用后版本为 1。其余二十七来源公开字段与开始前完全相同，Tushare 的 120 分配置保留，未包含权限没有探测。
- 只返回标题、发布方、原始链接、标识和明确发布时间。来源首次可用时间无独立证明，全部保留 null；不抓文章正文、图片或生成摘要。有限订阅始终披露 partial，不能当作全市场、完整历史或严格 PIT 证明。

### 来源、合同与消费验证

RSS 模块拥有固定 URL、解析与来源声明；公共执行器通过设置协议读取上限，继续拥有预算、进程并发/回收及实际尝试记录。无 Key 来源在新闻缓存绑定中使用现有 `not-required` 修订，旧 Key 来源的 HMAC 语义保持。控制快照、政策、精确准入、来源修订与撤销守卫继续生效。

| 范围 | 实际命令或步骤 | 结果与适用输入 |
| --- | --- | --- |
| DSA 相关回归 | `.venv/bin/python -m pytest`：`test_nvidia_news_rss.py`、`test_thesis_ledger_news_{cache,contract_v1,execution,fallback,gateway,http_acceptance,results,service,sources,transport}.py`、`test_data_search_isolation.py`、`test_thesis_ledger_capability_registry.py`、`test_thesis_ledger_manifest_source_facts.py`；参数 `-q --disable-warnings --tb=short` | 498 通过、4 条既有弃用提示，53.59 s；未变旧来源、控制和缓存证据复用。新增用例已登记数据测试清单。 |
| XML 编码复核 | `.venv/bin/python -m pytest tests/test_nvidia_news_rss.py -k utf16 -q --disable-warnings --tb=short`；随后修复并运行 `tests/test_nvidia_news_rss.py tests/test_thesis_ledger_news_execution.py` | UTF-16 实体绕过先失败；限定 UTF-8、禁止 NUL/DTD/实体并保留 UTF-8 BOM 后，36 通过、2 条既有弃用提示，16.55 s。 |
| 静态来源门禁 | 七个受影响 Python 文件的 `flake8`；`scripts/check_thesis_ledger_capability_boundaries.py` | 均退出 0；URL、512 KiB/50 条上限、日期未知、一次请求/不重定向、429/超时、零尝试与子进程边界均有定向用例。 |
| 共享合同 | `pnpm --filter @thesis-ledger/schemas exec vitest run test/market-news-v3.test.ts test/market-news-read.test.ts` | 60 通过；共享读模型复用 V3 条目/计数/引用/时间约束，严格绑定证券、窗口和条数。 |
| 服务端合同 | `pnpm --filter @thesis-ledger/server exec vitest run test/market/market-news-http.test.ts test/integration/dsa-news-v3.test.ts` | 61 通过；增加导出后先构建 Schemas，再验证下游，旧 dist 的初次失败不计通过。 |
| 客户端 | `pnpm --filter @thesis-ledger/desktop exec vitest run test/market-news-api.test.ts test/market-news-sheet.ui.test.tsx` | 3 项 API 与 2 项真实组件用例分别通过；显式提交、错误保留输入、无自动补试、切换取消和关闭释放通过。 |
| 工程 | Schemas build、Server typecheck、Desktop build、受影响文件 ESLint、`check-boundaries.mjs`、`check-workspace-dependencies.mjs` | 均退出 0；保留前端既有大包体积提示，没有调高阈值。最后的新闻标签调整已重新构建并在实际页面读取。 |
| 维护入口 | `node --test scripts/nvidia-news-admission.test.mjs` 与 DSA venv 的 `python -B scripts/nvidia-news-admission.test.py` | 3+4 项通过；默认计划、参数/身份/挂载/摘要拒绝、仅元数据、构造器无副作用、缺库不创建及并发不覆盖。 |

初次通过 `pnpm test -- ...` 传参误展开了全量运行，已中止且不作为本次完成证据；其间旧现金跨语言用例出现 5 秒超时，未修改其阈值或实现。随后使用上述锁定版本的明确文件入口验证本次改动，未声称全仓测试通过。

### 正式部署与准入

执行目录均为 `/Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra`，没有新增依赖、迁移或 Dockerfile 输入：

1. `node scripts/research-state-backup.mjs backup --target thesis-ledger-dev --output .../.maintenance-backups/free-news-dg3-20261022` 退出 0，封存 PostgreSQL、DSA SQLite/文件、回测与镜像标识；后续同入口封存 `free-news-dg3-20261022-after`。
2. `./scripts/sync-code.sh all` 完整退出 0，正式更新 DSA、Server 与 Worker。首次默认权限因无法查询进程而在取得锁/修改 Docker 前退出，随后保留原进程检查以足够权限执行。
3. 通过正式配置 API 仅启用新来源，再由 `node scripts/nvidia-news-admission.mjs` 的计划、`--apply` 和 `--verify` 入口登记/读回。参数为 `--confirm=thesis-ledger-dev/nvidia-news/NVDA.US/personal-noncommercial`、`--feed=<私有原始订阅路径>`、`--sha256=1f2e1b16c4091cf85e1ebe9975357378a1da5ef5352d0d6e9591030e62eb67ea`、`--observed-at=2026-10-21T19:28:17.663471+00:00`、`--start=2026-10-18`、`--end=2026-10-28`、`--valid-hours=168`、`--recorded-by=dg3-user-free-news-20261022`。证据仅安装元数据 JSON，SHA-256 为 `ff9d4b2c2c3f1ee22485b9faa633471d0fe627079a81e190e3f7d00b5f5a550e`，准入版本 1，有效至 `2026-10-28T20:01:19.004992+00:00`。
4. 首次准入写入后，脚本将挂载顺序变化误报为身份变化，退出 1；只读查询确认写入已完成，未重复应用。排序修复后正式 `--verify` 两次退出 0，原容器/镜像/挂载和版本 1 相同。此失败回执保留。
5. 新闻是独立功能，首个期望政策 167 因遗漏显式 `enabled` 被 DSA 拒绝；历史保留。补齐新路线的启用字段后，政策与 DSA 为 168、applied，原十六条内容不变，仅新增一条新闻路线。当前实际目录 complete，28 来源/190 精确目标，新目标 ready。
6. XML 编码修复后 `./scripts/sync-code.sh dsa` 退出 0。一次自动审批曾依据“测试尚未完成”拒绝启动；提交已完整返回的 36 项通过与 flake8 结果后，同一正式入口获准执行。没有绕过审批或验证。最终六个 DSA 生产文件与宿主源码 SHA-256 全部匹配，新解析器再次真实返回五条新闻。
7. 两次快更均只更新容器可写层，没有构建镜像、变更卷或数据库结构。DSA 镜像保持 `ebe9d9a742b396b5e885f007dedc49c960010094d095545be9b33ff2dfa8b632`；Server/Worker 保持 `6cf684b55348b9fe7e7869d5dedf5b50605c5b184602189d19f2034080f41140`。容器重建后需再次同步或通过正式完整更新固化代码，不将本次结果称为新镜像发布。

### 真实 V3、Market 与 Web

目标 `/api/v3/thesis-ledger/market/news` 与 `/api/market-data/news` 都取得五条非空数据，来源修订为 `nvidia-rss-news-v1:nvidia-official-finite-feed-v1`，凭据修订 `not-required`，政策 `effective:168`，每次实际检索一次上游请求。首个 Market 请求约 2.05 秒；最终编码修复后的目标再次通过。V3 请求身份与响应一致，五条 ID、标题、发布方、原文、发布时间、证据组与覆盖信息相同；各次独立抓取时间不要求相等，也未互相覆盖。

首条消息的原文为 [NVIDIA 官方博客](https://blogs.nvidia.com/blog/local-ai-gtc-rtx-dgx-spark-station-sync/)，RSS 的发布时间为 `2026-10-21T10:50:37+00:00`；实际页面显示北京时间 2026-10-21 18:50:37。五条均逐字段匹配原始订阅。覆盖为 examined=20、returned=5、uniqueEvidence=5、complete=false、truncated=true，保留 `issuer_feed_not_exhaustive` 与 `pagination_truncated`；全部来源首次可用时间和摘录为空。

实际 Web 使用宿主前端构建产物与 Docker API，用户显式点击后读取，无模型请求。侧栏显示发布方、原文链接、发布/抓取时间、未知可用时间及覆盖缺口；390/768/1440 宽度核对无横向溢出，长标题正常换行并由侧栏滚动，复用既有深色界面、字体和组件。最新构建的路由标签为“新闻检索”，控制台没有错误。切换为 MSFT.US 后显示“暂不可用”、零条、未准入原因，不沿用 NVIDIA 的旧结果。

正式 V3 对 MSFT.US、未配置的中国内地市场、范围外日期及不支持的游标均返回 unavailable、upstreamAttempts=0，前三项为 not_admitted，游标为 unsupported；没有为未满足范围强行发起来源请求。来源关闭、准入撤销、时间/预算边界和失败恢复另由定向测试持有，不重复扰动目标配置。

### 数据保护与最终状态

- PostgreSQL 从正式备份的 COPY 数据与目标逐表对比，83 表中 78 表完全相同。五处变化分别为期望政策状态、两条政策历史追加、已有健康检查追加/健康状态刷新，以及 AutomationJob 的 nextRunAt 正常推进；没有其他字段变化。七张原保护表及 57 个成功 V3 回测的完整状态与备份相同，Account=15；AiInvocation、AiInvocationFact、研究观察相关表仍为零。
- DSA 44 表结构完全相同，开始时 245 行中只有原政策状态行按预期变更。其余原记录保持，仅新增一条已应用政策历史、一条 NVIDIA 准入、一条新来源配置及一条来源健康记录。旧凭据密文行没有更新，维护入口没有调用会轮换凭据或初始化结构的普通构造器。
- 203 个原 DSA 文件内容不变，无删除或改写，仅新增本次元数据证据；回测归档字节完全相同。原二十七来源公开字段逐一相同，原十六路线保留。
- 最后一次 DSA 快更与真实读取后再次只读对账，44 表/249 行相对验收后备份仅新增来源的健康诊断四字段刷新；原四十三条健康记录及其他表均保持。Server、Worker 各三个实际加载的合同/Schema 产物也与宿主构建 SHA-256 相同，未将工作区路径误当作容器的 pnpm 安装位置。
- 私有结构化回执、原始订阅、配置前/后、实际请求/响应、哈希和页面图片位于主仓 `.recovery-artifacts/remaining-p1-2026-10-20/free-news-dg3-20261022/`；业务备份仅位于 infra 的两个受限目录。临时文件未单独充作通过依据。
- [最终复核](../../../thesis-ledger/docs/archive/reviews/2026-10-22-free-news-dg3-final-review.md)确认七项 AC。DG3 在本来源的明确范围内完成；DSA 原任务与本次任务归档，总表及来源矩阵同步。研究 G3、模型/费用与其他业务发布条件仍未通过，真实生成和 V2 写入没有开启。

<a id="dg3-existing-source-20261022"></a>

## 2026-10-22 复用现有 AKShare 来源

用户进一步明确来源顺序：先现有来源，再 GitHub 开源方案，最后才使用此前查找的方案。[配套规格](../../../thesis-ledger/docs/specs/2026-10-22-existing-source-news.md)按此完成；上节 NVIDIA 实源证据保留，当前增量为现有 AKShare 的 A 股新闻。此前七家专用搜索的配置盘点并不覆盖所有行情 SDK，不能据此判断现有来源均不能提供新闻。

### 来源与权限盘点

公共运行态共 28 来源，逐一读取配置及当前绑定，不读取或输出秘密。

| 来源 | 当前依据与结论 |
| --- | --- |
| AKShare | 已启用、无需 Key。官方[个股新闻文档](https://akshare.akfamily.xyz/data/stock/stock.html#id139)提供 `stock_news_em`。宿主 `1.18.94`、目标 `1.19.1` 的该函数源码 SHA-256 均为 `5c2edc3f07a0273e05817410f10ba1f2a9ffc4f365aa81449fdb70d9cbe4e3e5`；实际函数为第一页十条，不能按文档“一百条”宣称完整覆盖。采用此现有来源。 |
| Yahoo Finance | 已启用，安装 SDK 与[官方 `Ticker.get_news` 文档](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_news.html)具备新闻入口；AKShare 已成功，因此本轮不再启动第二来源的网络验证或准入。 |
| Tushare | 已启用、配置保留为 120 分，独立权限列表为空。[新闻快讯官方文档](https://tushare.pro/document/2?doc_id=143)要求单独开通新闻资讯；未调用受限接口，原可用功能保持。 |
| HiThink、Futu | 已配置并启用，但当前已验收绑定没有新闻，不能从行情账号推断新闻授权；没有为了补齐盘点调用未核准接口。 |
| Alpha Vantage、Finnhub、Longbridge、TickFlow、RQData | 当前关闭且没有配置凭据。RQData 保留用户指定的 TODO；本轮没有获取新账号或借用凭据。 |
| 七家专用搜索来源 | Tavily、SerpAPI、Bocha、Anspire、MiniMax、Brave、SearXNG 均关闭、无凭据/专属地址，沿用原盘点；没有公共实例可直接复用的证据。 |
| 其他既有来源 | 腾讯、BaoStock、efinance、pytdx、TickFlow 免费历史及本地状态、规则、现金公告，其现行登记均未提供新闻；不将公告或行情当作新闻结果。 |
| NVIDIA 官方订阅 | 现有启用与 `NVDA.US` 的原精确准入、路线不变，原证据继续有效。 |

已在第一层找到并实测可用来源，因此 GitHub 替代服务和其他新免费源的启动条件不成立；没有安装额外依赖、创建账号或开启模型。公开搜索的访问成功只证明本次元数据读取，不代替新闻正文的再发布授权。

### 实现与本地验证

- 新增 `akshare / eastmoney / CN / STOCK / NEWS_SEARCH` 一个精确绑定，AKShare manifest 为版本 5，新闻仅声明于 `eastmoney`。保留所有原绑定。公开身份、当前政策、修订、标的/日期和准入守卫继续适用；原 SDK adapter 携带固定快照用于执行前检查。
- 复用原 SDK 参数/字段解析及 `curl_cffi` 传输，移除其固定 Cookie，仅保留必要公开请求头，使用正确代码的 Referer。固定 HTTPS 端点、一次请求、禁止重定向，512 KiB、十条、现有总预算/并发/进程回收边界；SDK 版本和函数源码必须匹配本次核对范围。
- 初次将传输替换为 `requests` 时，目标虽为 200，却返回另一 JSONP 回调及 `passportWeb` 结果；未登记准入或改变政策。恢复 SDK 既有 `curl_cffi` 后，无 Cookie 的同一查询返回正确 `cmsArticleWebOld` 十条。超时异常构造要求也由定向失败修正，不放宽验证。
- 仅标题、媒体、转载页 HTTPS 链接、条目标识和时间进入公共结果。`originalUrl`、`sourceAvailableAt`、`excerpt` 均为空；中国本地时间按 `Asia/Shanghai` 解释，未知不补抓取时间。覆盖始终 partial，保留 `search_first_page_not_exhaustive`、`pagination_truncated`。
- Desktop 沿用原侧栏、组件和 TanStack Query，仅补规范 A 股/美股代码判断与中文范围说明，默认 `600519.SH`；未增加样式选择器、依赖或请求入口。

定向命令与结果：

1. `.venv/bin/python -B -m pytest -q tests/test_akshare_news.py tests/test_nvidia_news_rss.py tests/test_thesis_ledger_capability_registry.py tests/test_thesis_ledger_news_execution.py tests/test_thesis_ledger_news_sources.py`：402 通过，17.52 秒。后续仅传输变化，对来源及执行器重验 38 项，最终来源修正后 23 通过；执行器另外 15 项保持通过。警告为已有 Starlette 弃用及测试类收集提示。最初新测试的入口参数和 DSA 严格政策夹具写错、一次文件名不存在，修正后以上命令通过，不作为实现通过证据。
2. `pnpm --filter @thesis-ledger/desktop exec vitest run test/market-news-sheet.ui.test.tsx test/market-news-api.test.ts`：5 通过；覆盖 CN/US 映射、显式读取、失败重试、输入改变与关闭取消。Desktop build 通过，复用未改共享协议和 Server 的原验收。
3. `node --test scripts/nvidia-news-admission.test.mjs`：4 通过；`.venv/bin/python -B ../thesis-ledger-infra/scripts/nvidia-news-admission.test.py`：6 通过。正式维护入口复用锁、目标身份、摘要、范围、事务内首次登记与不覆盖规则，增加 AKShare 元数据配置档，不新增绕过入口。
4. 五个变更 Python 文件的 flake8 关键检查、能力边界、数据测试清单门禁通过；Desktop 定向 ESLint、主仓 import boundaries 通过。未重复运行未变输入的全库、共享合同或 NVIDIA 在线请求。

### 正式部署和真实消费

执行目录为 `thesis-ledger-infra`，入口均退出 0，未执行原始 Docker 状态变更：

1. `node scripts/research-state-backup.mjs backup --target thesis-ledger-dev --output <infra>/.maintenance-backups/existing-news-dg3-20261022`，完成 PostgreSQL、DSA、文件及回测备份。
2. `./scripts/sync-code.sh all` 完成已稳定绑定及消费产物同步；传输修正经定向通过后，`./scripts/sync-code.sh dsa` 只更新 DSA。全部五容器健康。DSA 镜像仍为 `sha256:ebe9d9a742b396b5e885f007dedc49c960010094d095545be9b33ff2dfa8b632`，Server/Worker 仍为 `sha256:6cf684b55348b9fe7e7869d5dedf5b50605c5b184602189d19f2034080f41140`；这是可写层快更，不是新镜像发布。
3. 目标实际适配读取：SDK 1.19.1，一次 HTTP 200、168 ms、十条元数据。审阅文件 SHA-256 `d92a6955288a659ebb9d4b70d0db9a782e4975f1c298f9a4d822bc7c74b75772`，观察时间 `2026-10-21T20:45:45.401681+00:00`。
4. `node scripts/nvidia-news-admission.mjs` 先计划、再 `--apply`、再 `--verify`。参数为 `--source=akshare-eastmoney`、`--confirm=thesis-ledger-dev/akshare/600519.SH/public-metadata-research`、`--feed=<已审阅元数据绝对路径>`、上述 `--sha256`/`--observed-at`、`--start=2026-10-18`、`--end=2026-10-28`、`--valid-hours=168`、`--recorded-by=dg3-existing-source-20261022`。仅新增准入版本 1；规范证据 SHA-256 `df185569f66df88ac621679616dceb30c028b28bd5aa8323d02d1f77e354a625`，有效至 `2026-10-28T20:45:55.082552+00:00`。该配置档标签只标识公开元数据研究用途，不表示第三方内容转授权。
5. 经现有 Market API 的 CAS，只追加一条显式启用的 CN 新闻路线。Server/DSA 均为 169、applied；原十七条逐字段相同。目录 complete、28 来源/191 精确目标，AKShare 新闻目标 ready。
6. 正式 Market `/api/market-data/news` 返回 partial、五条、一次尝试、1002 ms；认证 DSA V3 返回相同五条元数据、一次尝试、865 ms。首条为《10月21日13只股盘后交易额超500万元》，媒体证券时报网，发布 `2026-10-21T16:26:00+08:00`，来源页 `https://finance.eastmoney.com/a/202610213902666262.html`。窗口、标识、媒体、时间与已审阅样本匹配。
7. 实际 Web 使用宿主 Desktop 构建和目标 Docker API；显式读取显示五条、覆盖缺口、媒体、转载页链接及未知来源首次可用时间，控制台无错误。切换 `000001.SZ` 立即清除旧结果，读取显示未准入、零条；API 对该代码、2020 旧窗口与不支持游标均 unavailable、零尝试，错误分别为 not_admitted、not_admitted、unsupported。

### 保全与收口

- 前后正式备份路径为上述目录及 `existing-news-dg3-20261022-after`。PostgreSQL 83 表中 78 表的逐行规范摘要不变；五个差异表为期望政策、其历史新增一条、健康状态、健康检查新增一条及调度 `nextRunAt` 正常变化。COPY 与实际行规范化核对调度没有其他字段差异。Account=15、Position=1、LedgerEvent=85，AI 调用与研究观察相关表仍零；57 个成功回测及其归档字节相同。
- DSA 44 表结构相同；仅政策状态更新及政策历史、AKShare 新闻健康、精确准入各新增一条，所有原配置与凭据密文均保持。204 个原文件内容相同，只有一份本次元数据证据新增；未清库或删除卷。
- 原 28 来源中，只有 AKShare 的 manifest 版本、能力和上游能力字段按预期变化，其他 27 来源公开字段完全相同；Tushare 启用状态、120 分与原配置版本保持。
- 八个目标 DSA 生产文件与当前宿主源码 SHA-256 逐一相同。原输入与其他未提交工作未被覆盖；结构化回执、前后公共状态、真实响应、哈希及 Web 图片位于主仓 `.recovery-artifacts/remaining-p1-2026-10-20/existing-news-dg3-20261022/`，业务备份在 infra 私有维护目录。
- [最终复核](../../../thesis-ledger/docs/archive/reviews/2026-10-22-existing-source-news-final-review.md)持有 E-AC1～5 结论。本次完成现有来源的明确 A 股样本范围；其他股票须具备自己的精确准入，当前日期及有效期不会自动扩展。研究 G3、模型/预算/发布条件仍由原任务持有。


