# ThesisLedger M2 来源准入门禁与证据入口

## 2026-10-03 当前共同价格基线

按用户确认，基础 ETF 日线以 HiThink 和腾讯的共同价格能力为准。`hithink/fund-market-historical` qfq 与 `tencent/tencent` none/qfq/hfq 根据适配能力、启用状态、必要凭据和精确路由就绪，不再查询逐标的、逐窗口、短期失效的人工 RouteAdmission。HiThink 必须有真实 API Key；腾讯不要求 API Key。错误目标、错误口径、无效价格、重复日期和不完整分页仍由正常读取校验处理。

公共日线层从实际返回的 Bar 和日历自动整理交易日状态、采集时间和归一化响应摘要；不再要求腾讯提供 HiThink 专用 DataFrame 属性。缺日只按显式研究假设不交易，不填补价格或证明停牌。基础图表与固定快照归一化研究允许同口径整窗备用，实际来源随结果保存；严格 PIT、真实份额、事件和单位敏感规则仍按各自依赖开放。

官方离线门禁 7752 项通过；目标 HiThink qfq、腾讯 qfq/hfq 普通回测及冻结重放均成功。当前结果与后续备源验收见主仓[共同价格能力验收](../../thesis-ledger/docs/tasks/evidence/2026-10-03-common-price-baseline.md)。下文窗口准入、元数据阻塞及旧请求预算保留为历史记录，不代表当前基础能力。已明确跳过的收费、无账号、不可用来源继续跳过。

## 2026-10-03 腾讯 ETF 后复权适配

腾讯 ETF HFQ 最小适配已完成，既有 `tencent/tencent` 精确入口读取 `newfqkline/get` 原生 `hfqday` 和成交额。定向 120 项、官方离线 7732 项通过，官方 DSA 代码同步完成。后续目标 159516.SZ、2026-04-30..08-09 完整窗口 68 日和重叠窗 10 日核对通过，取得仅该标的/口径/窗口的价格研究准入，有效至北京时间 2026-10-10 01:17:22；正式 Data V3 HTTP 200、68 行，越界反例 422。Server/DSA policy 均为 revision 33，完整目录中 HiThink qfq 和 Tencent hfq 两个精确条目 ready。

腾讯的量额单位、成交量复权语义和分红经济语义保持未知或供应商定义，不授予严格 PIT、真实份额、事件完整性或独立备用兼容。正式 Server 回测准备 HTTP 201、业务 blocked；固定快照日线还要求 `daily_tradability_input`，当前腾讯适配器没有该元数据。目标离线复现通过、零网络请求，未创建 Run、未执行重放，停止同条件来源重试。详见主仓[价格研究准入与目标验收](../../thesis-ledger/docs/tasks/evidence/2026-10-03-tencent-hfq-price-research.md)。其他来源的跳过决定保留。

## 2026-10-02 当前执行状态调整

通达信与 a-stock-data：按用户顺序，TdxAiData 付费后台的 M27/M28/M34 当前跳过。目标匿名探针取得腾讯 `159516.SZ` 的 none/hfq 各 68 行，但旧 `fqkline/get` 六列缺原生成交额，不能通过当前 `newfqkline/get` 的九列/分页合同，ETF 清单也尚无 hfq；新浪固定版 `raw × f` 算法不能表达该 ETF 拆分。因此本次 a-stock-data ETF 回测接入也标记 **TODO／合同不满足／当前跳过**，保留成功取样及盘后包证据，不改变既有腾讯能力。未新建 Provider 或授予准入，详见主仓[免费接口实测与停止点](../../thesis-ledger/docs/tasks/evidence/2026-10-02-astockdata-free-path-validation.md)。

Tushare：用户确认当前使用的免费版本不提供 fund_daily、fund_adj、fund_div 所需能力，M24/M25/M26 未完成部分标记 **TODO／当前免费版本不支持／当前跳过**，依赖这些接口的 Tushare 派生路径暂停。保留已保存的页面凭据、已完成本地接线和目标准备验证；相同版本/权限下不重复请求，变更后重新核验。版本限制来自用户确认，目标直接证据为三个接口的 40203 权限拒绝，不外推其他账号或接口。见主仓[配置、权限与执行决定](../../thesis-ledger/docs/tasks/evidence/2026-10-02-tushare-page-credentials-target.md)。

按用户要求，AKShare、EastMoney 在 M2 中尚未完成的行情、拆分与分红验收（M21、M22、M23 及未完成子叶）标记为 **TODO／暂不可用／当前跳过**。停止本轮来源请求及准入推进，保留已完成实现、观测和映射草案。既有目录能力和运行时配置不变；恢复验收须补齐新的可用性、单位／日期／覆盖等合同证据。当前执行顺序转到其他来源，账号或权限未变化的既有拒绝不重复请求。

## 2026-10-02 其他来源前置复核

用户随后要求先验证通达信免费路径：目标 ARM64 容器匿名下载 2026-07-09/10 官网盘后包均 HTTP 200，两包均命中 `159516.SZ`、`510300.SH`，结构与目标 OHLC 检查通过；ETF 量额单位、逐行日期、完整历史、复权因子和公司行动仍未准入。客户端方式缺必要终端和模块。只读调查完成，未安装 SDK 或接入生产；见主仓[实际验证与边界](../../thesis-ledger/docs/tasks/evidence/2026-10-02-tdx-free-access-validation.md)。

通达信后续架构核验：在目标 DSA 首次读取固定 tdxaidata 1.2.2 wheel 成功，SHA-256 匹配；默认 Linux 库 ELF64/e_machine=62 为 x86_64，与当前 aarch64 容器不符。M34-sdk-artifact-header 已完成，SDK 集成继续阻塞；未安装、执行 SDK 或改变镜像。用户已选择申请通达信数据服务 Key，官方商城与创建路径已核对；浏览器连接超时，申请待用户注册/登录并确认入口，尚未创建账号或 Key。见主仓[固定包架构证据](../../thesis-ledger/docs/tasks/evidence/2026-10-02-tdxaidata-sdk-architecture.md)。

后续用户授权处理 Tushare 页面配置：zsh 已有 Token 经前端同一 Server→DSA 配置 API 保存，HTTP 201；registry 读回 configured=true、source=control、configVersion=3，目标密文及实际凭据快照读取通过。目标用该快照对 fund_daily/fund_adj/fund_div 各做一次有界 HTTPS 验证，均为 200/40203；配置缺口已解除，基金权限、覆盖和准入继续开放，不重复同条件请求。见主仓[目标配置与权限证据](../../thesis-ledger/docs/tasks/evidence/2026-10-02-tushare-page-credentials-target.md)。下段保留首次前置观察。

当前其他来源前置复核：目标 Control registry HTTP 200，Tushare/RQData 未配置，HiThink 环境凭据已配置；没有新的基金权限事实，不重试旧 40203。HiThink 公开合同仍未解释 progress="2"。官方通达信后台候选为 tdxaidata 1.2.2，目标 ARM64 尚无该 SDK，固定 wheel 唯一下载超时，ABI/授权未核实。已有本地接线不重做，真实门禁保持开放，详见主仓[剩余来源前置证据](../../thesis-ledger/docs/tasks/evidence/2026-10-02-priority2-provider-prerequisites.md)。

## 2026-10-02 M21 目标三口径长窗观测

续接：分阶段探针新请求明确在目标EastMoney价格HTTP阶段失败，后续同端点请求暂停。独立M22真实拆分Reader一次读取1页83行、目标两次拆分与旧观测一致；两份管理人实施公告原字节摘要复核一致，已形成精确映射草案。真实观测在隔离离线审核上下文经现有resolver核验，31项相邻守卫通过；未写目标准入、业务覆盖仍false。原文、映射摘要及后续门禁见主仓[续接证据](../../thesis-ledger/docs/tasks/evidence/2026-10-02-priority2-m21-m22-continuation.md)。

目标AKShare 1.19.1的 `fund_etf_hist_em` 对159516.SZ、2026-04-30..2026-08-09首次none/qfq/hfq均HTTP200、68条唯一日期，原响应代码/市场与请求匹配；三口径原生量额总和相同。深交所独立68日聚合量完全匹配、额相差1.74元。后续有明确逐日对账目的的none请求遇ConnectionError，同端点其余口径暂停；逐日单位、日期全集、复权基准、连续可用性及准入仍未通过，生产ETF单位保持unknown。完整请求摘要、边界和接续输入以主仓[第二优先级首叶证据](../../thesis-ledger/docs/tasks/evidence/2026-10-02-priority2-m21-source-window.md)为准。本轮只更新证据，无生产适配、策略、数据库或部署变更。

## 2026-09-27 目录股票身份补充

Catalog 原规则将所有 9 开头代码映射 SH，现将 920 优先映射 BJ，并让 AKShare 股票目录入口显式指定 STOCK。依据为[北交所 2024-04-19 官方公告](https://www.bse.cn/important_news/200021629.html)：920 号段功能自 2024-04-22 启用。旧代码仍按输入原样保留，不自动推断新旧代码换算或历史上市有效期。

`test_thesis_ledger_catalog_sources.py` 覆盖 BJ 920/旧 4/8、SH A/B 股、SZ、AKShare 三个实际 loader 接口字段以及缺列拒绝。全部使用离线响应，不能替代来源指纹、权限或全量目录准入；R01.1 仍开放。

验证：目录来源、进程隔离和目录作业共 18 项通过，关键错误 flake8 通过。目标容器尚未同步此目录修复；需等原完整构建终态后按实际源码差异核对。

后续重复冲突收敛：目录构建器先完整校验单来源快照，再并入聚合结果；完全一致的重复身份幂等，同一来源名称等内容不一致则报告 `catalog_provider_invalid_response` 并丢弃该来源全部输入，不留下冲突前的部分记录。其他来源仍可成功，跨来源优先级仍按已配置 loader 顺序。新增原子拒绝、幂等及跨源优先级回归，目录相关共 21 项通过，关键错误 flake8 通过。

缺失输入收敛：实际复现 NaN 名称被转换为字符串 `nan`；缺失代码还可能被补成零代码。现拒绝缺失/全零/非 ASCII/布尔代码及非文本或空白名称，不跳过坏行生成部分来源快照。新增 8 项反例后目录相关共 29 项通过，关键错误 flake8 通过。目标同步仍须独立核验，当前目录也不代表历史上市状态。

## 2026-09-27 XSHG 固定版本与特殊休市样本

后续修复已实现：`thesis_ledger_calendar_release.py` 离线核验实际包的 93 个 Python 文件及源码树摘要，绑定官方 wheel 发布时间 `2026-03-10T03:24:37.055242Z`。`calendar_fact` 以此作为保守 availableAt，适配修订增加 `release-evidence-v1`，不再按请求起日回填。未知版本、源码缺失/增加/修改/符号链接、dataAsOf 早于发布时点均拒绝；发布/HTTP/日历测试 32 项通过。该变更未重写旧快照，目标更新尚在运行，部署后仍须核验。完整依据和后续任务见主仓 `docs/specs/2026-09-27-calendar-release-availability.md` 与配对 Task。

`requirements.txt` 已将 exchange-calendars 固定为宿主和目标当前实际验证过的 4.13.2。实际包测试共 4 项通过，包括普通休市、交易时段、首末覆盖边界、缺包拒绝和临时休市样本。

独立依据：[上交所 2020-01-27 调整公告](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20200127_4991580.shtml) 将春节休市延长到 2 月 2 日，2 月 3 日恢复交易；[原年度安排](https://www.sse.com.cn/disclosure/announcement/general/c/c_20191220_4969627.shtml) 原定 1 月 31 日恢复。当前包将 1 月 31 日列为休市，符合修订后事实，但不能将这项修订暴露到公告之前。当前 calendar_fact 的 availableAt 按请求起日产生，历史可见性缺口仍保留在 R01.10，不能因此宣称 PIT 验收通过。

依赖文件已变化，下一次目标部署必须使用 infra `./scripts/update.sh dsa`；不允许以当前容器版本恰好相同为由绕过完整更新。此次尚未构建镜像。专题无对应英文版。

## 2026-09-27 后续结算日历 HTTP 范围

`/v2/calendar` 使用独立 `validate_calendar_range`：start 不晚于 dataAsOf，end 最多晚 104 个自然日，实际日历包覆盖之外仍不可用。价格及其他经济事实继续使用原日期限制。此变更修复 Server 已扩展结算范围但实际 HTTP 入口仍拒绝的问题，不授予日历包历史发布时间证据；R01.10 继续保留可见性缺口。配对实施记录位于主仓结算日历 Spec/Task 的 C6。

## 2026-09-27 拆分映射比例合同对齐

DSA 与 Server 冻结入口统一使用普通十进制文本：整数部分不允许前导零，小数点后必须有数字，不接受指数、前导加号、空白、下划线和非 ASCII 数字。数值仍须为有限正数，金额与比例不经过浮点转换。修复 DSA Decimal 解析过宽导致已接受证据在消费端被拒绝的问题。

`tests/test_thesis_ledger_split_mapping_v3.py` 23 项通过，关键错误 flake8 与 py_compile 通过。未执行目标部署或真实准入；原合法十进制证据不变，非规范证据须重新制作并重新审核摘要。回滚仅涉及该输入格式校验，但会恢复跨服务不一致，因此不建议单独回滚来源端。专题没有对应英文版，无需同步英文文档。

## 2026-09-27 M21 原生字段契约补充

映射响应现携带 `dateMappingEvidence` 的引用、摘要及 UTF-8 原文；共享合同与 Server 在线/离线入口均已接入校验，原文经 Parquet 往返测试保留。DSA 事件回归 21 项、主仓映射 7 项及相关观测/选择回归通过。旧严格消费者会拒绝新字段，因此先更新消费者再更新 DSA；未部署，完整事件覆盖和真实快照执行仍开放。

拆分运行时已接入独立 SPLIT_EVENT 库存与修订；仍需单独准入。映射文件位于 Control 数据库同目录的 `thesis-ledger-mapping-evidence`，仅按当前准入 sha256 引用加载；内存 Control 不支持该存储。加载失败不请求 Provider，读取后继续复核撤销/策略变化。源日期查询可扩展至请求除权窗口之前，记账日期来自已绑定映射，登记日期必须另有明确字段，实际 observedAt 不改写。SQLite 执行与相关回归 58 项通过；生产部署、完整覆盖及跨服务映射原字节冻结仍待验收。存入文件不等于准入，目录中不得放置凭据。

映射证据存储子叶已完成：`thesis_ledger_mapping_evidence_store.py` 提供本地原子发布和按 SHA-256 引用读回，拒绝大小超限、篡改、符号链接与任意路径。7 项存储测试与 15 项映射测试通过；运行时当前准入接线尚未完成，没有真实准入变更。此中文专题无对应英文文件。

M22-c3 日期映射校验子叶已实现 `thesis_ledger_split_mapping_v3.py`，绑定原字节摘要、当前拆分准入、标的/源日期/比例、经济日期范围及公告引用。15 项测试通过；只增强观测，不生成执行事实或回填历史时间，受控证据存储与实际路由接线尚未完成。中文专题无英文对应版本。

M22-c1 有界读取已完成：`eastmoney_fund_split_reader.py` 与分红共用事件端点传输，限制超时、大小、五年窗口和跨年总页预算，拒绝分页漂移与重复。拆分/分红相关 59 项测试及 lint、编译通过。2026-09-27 真实单页核实 159596 的源记录为 2025-10-17、份额分拆、每份 2，与管理人公告相符；共 96 行、页信息 `[1,100,1]`。仍不映射开盘生效，历史覆盖不完整，不授予准入。完整哈希与观测时间见主仓 `docs/tasks/evidence/2026-09-27-etf-split-date-sample.md`。中文专题无对应英文文件需要同步。

M22 结构化拆分观测：`data_provider/eastmoney_fund_splits.py` 保留 fund_cf_em 的源折算日、类型、每份比例、观测时间和来源修订。生效阶段未知时返回 `split_effective_phase_unverified`，没有 canonical SPLIT、完整覆盖或策略可见性。16 项离线测试与 flake8 通过；没有真实 Provider 调用，读取器与生效阶段映射另验。仅更新中文专题，无对应英文专题需要同步。

V3 完整窗口和图表响应现已复用 `api/thesis_ledger_source_basis.py` 投影 `fieldUnits`，并校验来源、端点、资产、口径与未换算标记。缺省元数据不补单位，错配拒绝，显式单位纳入指纹，数值维持原生。新增单位测试与窗口/图表回归合计 54 项通过，受影响 Python 文件语法编译通过；主仓另已通过单位的真实 Parquet 冻结、读回与离线重放测试。尚未部署，需先更新主仓严格契约消费者。此专题无对应英文版本，英文首页不受影响。

精确东财 Fetcher 在最终 DataFrame 的 `native_daily_contract` 属性保留实际端点、assetType、adjustment/nativeAdjust、独立上游身份和字段单位。股票 `stock_zh_a_hist` 的官方输出表明确成交量为手、成交额为元；ETF `fund_etf_hist_em` 输出表未标注这两项单位，因此 ETF 继续记录 unknown，不能按股票值外推。依据：[股票官方文档](https://akshare.akfamily.xyz/data/stock/stock.html)、[ETF 官方文档](https://akshare.akfamily.xyz/data/fund/fund_public.html)，核对日期 2026-09-27。

三个 adjustment 逐一保留，`none` 映射原生空字符串。AKShare/EastMoney 的独立来源身份仍为 eastmoney，不算两个独立备用。`volumeAdjustment` 保持 unknown，`valuesConverted=false`；本次没有改变数值、声称已标准化股/份，也没有授予来源准入。

实现：`data_provider/akshare_daily_contract.py` 和现有精确 Fetcher 接线。股票/ETF × 三口径及无隐式 fallback 定向 8 项通过；连同超时和 V3 响应回归共 36 项通过。命令：`.venv/bin/python -m pytest tests/test_akshare_exact_eastmoney_contract.py tests/test_akshare_history_timeout.py tests/test_thesis_ledger_market_v3.py -q`。既有大 Fetcher 净减少行数，未新增平行抓取入口。

M21 尚需在消费契约中明确原生单位与标准单位转换边界、核验 ETF 单位与三口径真实证据；官方参数解释不替代目标历史覆盖、算法基准或真实准入。未运行目标部署。本补充只更新中文专题和中文变更条目，不变更英文首页；不存在同名英文专题需同步。

> 文档性质：可独立执行的只读准入计划。本文建立门禁和记录格式，不表示任何来源已经授权、可用或覆盖目标区间。
> 盘点基线：以 [ThesisLedger 有限来源能力目录](./thesis-ledger-source-capabilities.md) 所记 DSA `f497b6da`、主仓 `fe0e871e` 为初始输入；后续本地实现、manifest 变更与核验记录见下文，真实准入与离线证据分别记录。两仓当前工作区状态可能已变化，执行前须重新核对。

## 1. 范围与准入规则

本计划对应主仓 [Spec §3.2、§4、§5、§6](../../thesis-ledger/docs/specs/2026-09-25-multi-source-adjustment-aware-backtest.md) 和 [Task §8 G0-M、§9 M2](../../thesis-ledger/docs/tasks/2026-09-25-multi-source-adjustment-aware-backtest.md)。M2 的执行叶子是 M21–M31、M32–M34 及最终 `G-M2-Price`。本文件覆盖 M21–M30 所需的 AKShare/EastMoney、Tushare、官方 TdxAiData、RQData 和已登记且可能用于 M2 价格路径的现有上游；M31 HiThink 已由 G0-H 管理，不在此重复探测。

准入单元按 `Provider × 实际上游 × 资产 × 能力 × 周期/口径` 拆开。股票与 ETF、none/qfq/hfq、Bars/因子/拆分/分红/公告、不同实际 endpoint 都是独立结论。同一单位可共享一份经脱敏的响应指纹，但不能把一个单位的覆盖、权限、单位或通过结果外推给另一个单位。

- 本文状态初始为“待执行”或“选择阻塞”。目录中的 manifest/SDK 声明不等于真实账号授权、套餐额度、数据覆盖或可运行性；没有探针证据的值保持“未知”。
- 读取凭据只核对外部执行环境是否已配置，以及调用者确认的授权状态；不输出、不复制、不写入证据文件任何 Token、Key、Cookie 或完整环境变量。未获授权不得发请求。
- 每次探针限定单资产、单能力、只读、可复现窗口；记录实际 endpoint/协议身份与脱敏 source fingerprint。权限拒绝、无数据、能力不支持、限流、网络错误、解析错误必须分别分类，失败后不得静默切换上游。
- “通过”只对该原子单位、该权限范围和该观察窗口有效。未知事件可见时间、复权基准、货币/成交量单位、覆盖边界或来源身份都不能按“看起来合理”补齐。
- 同一底层来源不算独立备用：AKShare/Efinance 的 EastMoney 路由先视为同源；AKShare/Tencent 与 TencentFetcher 的腾讯路由先视为同源；仅当协议、实际服务身份和请求指纹证明来源不同后，才可重评。官方 TdxAiData 与社区 Pytdx、a-stock-data 候选分开登记，但也不能仅凭 SDK 名称宣布独立。

## 2. 固定样本和公共断言

### 2026-09-27 Tushare 精确 ETF raw 读取入口

`TushareFetcher.get_daily_data_for_source` 已提供仅 `tushare/ETF/none` 的精确读取入口，要求显式日期窗口；每次读取创建独立 HTTP client，各日期分段共享剩余时间预算，保留既有客户端配置。配额耗尽直接拒绝，不进入通用的一分钟等待；权限/传输失败不自动重试、切换端点或返回部分数据。分段证明记录实际请求数及规范化窗口，交易日覆盖仍未核验。16 项新增及既有回归共 67 项离线测试通过。

此入口已进入 V3 gated inventory，范围仅 CN ETF/none。manifest 登记 ETF 日线；实际目录和执行均需当前准入。内部 HMAC 同时绑定环境 Token 和冻结的接入地址，缓存随修订失效，同一修订继续累计限流计数；读取后再次核验修订与撤销状态。运行时与 API 复核真实分段数、连续窗口、上限、行数和摘要，再独立比对交易日覆盖。214 项接线回归与 81 项旧路径/凭据兼容测试通过。

宿主当前执行环境只读预检显示 Tushare Token、自定义地址与准入主密钥均未配置；没有发出真实请求，也未写入任何目标准入。目标 Docker 的既有结构阻塞仍独立存在。真实权限、目标数据覆盖与 G0/G-M2-Price 保留未通过，不能用离线样本代替。

### 2026-09-27 基金因子原始读取

已依据 [fund_adj 官方说明](https://tushare.pro/wctapi/documents/199.md) 固定单基金、日期窗口和因子字段，按每段最多 366 日、默认最多 32 段读取；单段请求 2000 行上限，仍按一天至多一行拒绝异常响应。精确 Fetcher 入口只允许 CN ETF 与 tushare 来源，不调用股票 adj_factor，不重试或切源。

HTTP JSON 的因子数值直接解析为 Decimal，结果保存十进制文本；缺字段、错标的、范围外/重复日期、非正或非有限因子均拒绝。实际 observedAt、逐段摘要和内容指纹可供后续冻结，供应商修订、锚点和算法修订保持未知，conversionAvailable=false；不从因子下降推导拆分事件。25 项新增及 raw/V3/凭据/分页回归共 152 项通过，6 项既有警告；flake8、py_compile、diff check 通过。

上述接口说明未给出足以核验基金锚点与历史修订的公开定义；另核对的 [官方仓库 data_pro.py](https://raw.githubusercontent.com/waditu/tushare/master/tushare/pro/data_pro.py) 只在股票分支使用 adj_factor，所读版本未提供 fund_adj 转换依据。该文件不是当前所有发行版的证明，不能据此推断基金转换关系。M25-b 的精确因子合同/准入/冻结和 M32 派生仍待完成，M25-c 真实权限/覆盖未执行。

### 2026-09-27 基金分红读取与标准化

依据 [fund_div 官方字段说明](https://tushare.pro/document/2?doc_id=120) 实现按基金代码的单次精确请求，不发送未证明的历史范围或分页参数；在本地按除息日选窗。共享限流/超时预算，默认最多接收 2000 行、可显式提高至 10000 行，该数值是本地预算而非上游分页承诺。超限拒绝，响应不切片、不重试、不切源，coverage.complete 始终为 false。

结构化观测分别保留公告/实施公告、收益基准、登记、除息、派息、收益支付、净值除权及红利再投资到账日期。div_cash 按每份金额保留，分红币种必须由调用方提供已核验事实，不能用 base_unit 的万份单位再次缩放现金。预案/取消/未知状态只保留观测；相同生效日出现冲突实施事实或计划状态时拒绝隐式选版本。

仅有日期的实施公告使用 conservative-day，取实施公告与公告日期的较晚者；availableAt 保留真实抓取时间，缺实施公告日不生成最终事实的策略可见性。ETF 仅接受明确交易所代码，净值基金使用现有 NAV_FUND 类型；不自动将 OF 与 SH/SZ 同数字代码互换。JSON 金额直接解析为 Decimal，原始响应内容指纹与未知供应商修订分开记录。

精确历史请求拒绝 HTTP 重定向，确保冻结的服务地址不会被自动跳转替换；该约束共用于 raw、因子和分红入口。27 项标准化、10 项精确读取新增测试，以及 raw/因子/凭据/V3 回归共 123 项通过，3 项既有警告；flake8、py_compile 通过。M26-a/b1 本地完成，M26-b2 的历史覆盖、事件 V3 准入与冻结接线、M26-c 真实权限/独立公告核验仍待完成。未发出真实 Provider 请求或写入目标准入。

### 2026-09-27 公告索引覆盖修复

M23-a 已实现 `data_provider/eastmoney_fund_dividends.py` 的独立结构化标准化。[官方基金分红说明](https://akshare.akfamily.xyz/data/fund/fund_public.html)明确 `fund_fh_em` 的分红为元/份，登记日、除息日、发放日各自独立；本地安装 SDK `fund_fhsp_em.py` 的列映射一致。模块按除息日生成经济事实，以实际观测时间保存 `availableAt`；没有公告字段，不生成 `strategyVisibility`。原始三种日期保留在独立观测记录中，缺登记/发放日保持未知，不从其他日期补齐。18 项离线测试覆盖金额、不完整空集、代码过滤、未知日期、错误日期及冲突重复；没有发出网络请求。

运行接入仍待 M23-b：当前标准化不签发覆盖完整性，不自动替换旧公告索引。已安装 SDK 的请求不带 timeout，且响应解析使用 `eval`；后续精确接入必须提供有界请求与安全解析，不能把 SDK 的 `page=-1` 声明直接当完整分页证明。拆分接口 `fund_cf_em` 的“折算日”与经济生效日映射仍待权威样本核实。

M23-b1 已增加 `eastmoney_fund_dividend_reader.py`，直接读取固定东财 endpoint，严格 JSON 字面量解析，不执行响应脚本；单页最多 1 MiB，连接/读取超时 5/15 秒，禁止重定向，默认总预算 10 页、显式最多 100 页，范围最多五年，失败不自动重试或切源。页数/容量变化、重复页面、中间缺页立即拒绝。传输页证明和历史覆盖分开，标准化结果仍 `coverage.complete=false`。31 项离线测试及 py_compile/flake8 通过。

M23-b2 日期传输接缝已完成：标准化的 canonical fact 增加已知 `recordDate` / `paymentDate`，未知时省略；主仓共享合同接收并冻结两字段，旧事实兼容。主仓冻结依赖测试验证篡改发放日会使证据校验失败。精确事件路由、准入及实际运行仍未接通。

后续接线：新增 `/api/v3/thesis-ledger/market/events` 与 `thesis_ledger_event_v3.py`。精确 `CN/ETF/CASH_DISTRIBUTION × akshare/eastmoney` 已进入 V3 库存，默认未准入；复用 SQLite 准入及当前适配/来源/凭据修订比较。读前读后必须满足同一策略、目录、准入及窗口，撤销、禁用、更新适配、策略变化均拒绝晚到结果。HTTP 使用现有合同 Token。当前来源缺完整历史证明，返回 `coverage.complete=false`；没有修改任何真实准入记录或执行网络请求。Server 消费面尚待接入，旧 V2 公告入口保持既有保守行为。

2026-09-27 单次公开只读探针返回 HTTP 200、12984 bytes，2025 年首屏元数据 `pageinfo=[75,100,1]`；确认响应变量布局，没有抓取其余 74 页，没有目标基金事件覆盖或历史修订准入结论。精确事件路由/冻结消费者接入继续归 M23-b2。

`normalize_etf_corporate_actions_v2` 不再根据公告日期和标题推断完整无事件窗口。索引没有生效日、金额/比例及完整分页证据，窗口前公告也可能在窗口内生效；因此索引入口返回不完整覆盖。M22/M23 仍需接入结构化事件、独立日期与单位证据；本修复不把缺失事实补成拆分或分红，也不开放事件依赖回测。旧依赖错误完整覆盖的请求将被拒绝，这是预期兼容性收紧。

### 2026-09-27 精确 EastMoney 适配核验

- [AKShare 官方 ETF 历史接口说明](https://akshare.akfamily.xyz/data/fund/fund_public.html#etf)明确 `fund_etf_hist_em` 使用指定周期、起止日期和 `adjust`，空值、不复权；`qfq`、前复权；`hfq`、后复权。输出表未注明成交量和成交额单位，因此本次不生成单位换算或基准兼容证明。
- 本地精确请求测试覆盖股票/ETF × none/qfq/hfq 六种组合，保留实际 endpoint 分派、标准化和指标处理，验证起止窗口、原生参数、实际 `eastmoney` 来源及量额值不被猜测换算；另验证两个资产入口超时后不调用备用来源。`tests/test_akshare_exact_eastmoney_contract.py` 共 8 项通过。
- 这关闭了 M21 的本地 endpoint/参数映射核验子叶，不关闭 M21、AK-00 或真实 G0；上游单位、算法/基准、修订、真实账号及窗口覆盖仍须独立证明。测试没有发出 Provider 请求。另一包装库使用 EastMoney 不能增加独立上游数量。

| 样本 ID | 计划样本 | 用途与限制 |
| --- | --- | --- |
| `B-ETF-01` | `159516.SZ`，日线，`2026-05-16..2026-08-09`，Asia/Shanghai 日期边界 | 目标 ETF 价格口径。各来源均须独立证明支持 ETF；预热区间由所选策略计算，具体起点当前未知。不得以另一标的代替目标验收。 |
| `B-STOCK-01` | `000001.SZ`，日线，`2026-05-16..2026-08-09`，Asia/Shanghai 日期边界 | 仅供 P02 明确登记为 CN STOCK 的 TdxAiData 等股票能力验证；不能代替 `B-ETF-01`。 |
| `E-CASH-01` | `510300.SH`：登记日 `2025-06-17`、除息日 `2025-06-18`、发放日 `2025-06-27`、税前现金 `0.880 CNY / 10 份` | 主仓 G0-H 已记录上交所托管基金管理人公告。它是待测正样本候选，不证明任何 M2 Provider 覆盖该事件。Provider 查无记录时必须先区分无权限、接口不覆盖和真实缺行。 |
| `E-SPLIT-01` | `159596.SZ`，2025-10-17 日终登记变更，每份拆为 2 份；[实施公告](https://static.cninfo.com.cn/finalpage/2025-10-14/1224707357.PDF) 明确交易除权日为 2025-10-20，[结果公告](https://static.cninfo.com.cn/finalpage/2025-10-20/1224719474.PDF) 确认完成；核实于 2026-09-27 | 已获取东财对应行，日期与比例匹配。源日期与交易除权日期分别保留；映射证据接线、历史可用时间和准入仍未完成，不回填或按下一交易日猜测。 |
| `E-NONE-01` | 目标标的和无事件区间：**未知** | `510300.SH` 的 `2025-07` 仍未由完整公告检索证实为无事件区间，不得用作负样本。负样本须先完成独立公告范围核查。 |

Bar 覆盖使用同一版本的 XSHG 交易日历生成预期日期集合；G0-H 曾使用 `exchange_calendars 4.13.2`，M2 执行时须重新核对并记录实际版本、交易日集合指纹和时区。对比请求须检查页数/截断、起止边界、重复、缺日、重叠窗口逐 Bar 一致性、字段空值和响应版本。接口未说明最大窗口或历史保留边界时记“未知”，不把一次无分页标志的响应当作完整历史证明。

所有价格样本均核对 OHLC 约束、正数/空值、代码与交易日、成交量、成交额、币种、数量坐标及其来源字段。中国市场预期币种为 CNY，但必须记录来源字段或权威契约证据；不得从市场代码推断成交量是股、手、份或拆分调整量。复权 Bar 还须记录 Provider 算法/响应标记、基准范围、锚点、观察时间、修订指纹、数量/成交量口径和历史窗口敏感性。重复取同一结束日、不同开始日的重叠窗口，用于发现 hfq/window 基准变化；任何差异都需解释或阻塞。

事件样本需把事件种类、比例/现金金额、登记日、除权/除息生效日、支付日、公告发布时间、Provider 首次可见时间、抓取时间分列。无可见时间时可在有证据的前提下作为记账事实候选，但不得用于历史决策信号；公告索引不能冒充拆分/分红事实。调整因子不是完整事件表。

统一错误断言：无权限不等于空数据；未支持资产/字段不等于 Provider 故障；成功响应的空集也不能证明无事件；超时/限流/服务错误不得触发另一来源的静默 fallback；字段、日期或单位无法解释时结果为 unavailable/blocked，不生成猜测值。所有接口在被证明为只读及低请求量前，不执行重试风暴、宽市场扫描或批量下载。

## 3. G0-M 原子子门禁

表内 `通过条件 / 阻塞条件 / 当前状态` 是该行自己的结论边界。`BAR` 表示继承本节价格、覆盖、单位和错误断言；`EVENT` 表示继承事件样本、时间分离和错误断言。继承公共断言不会合并各原子行的执行或结论。

### 3.1 AKShare / EastMoney

| 门禁 ID | P02 原子来源/能力与样本 | 权限与覆盖 | 单位、窗口/复权、事件时间 | 通过条件 / 阻塞条件 / 对应 M2 叶子 / 当前状态 |
| --- | --- | --- | --- | --- |
| `AK-00` | `akshare/akshare` Provider dispatch；实际 endpoint 未知。先分别绑定函数、上游和 source fingerprint；ETF 样本固定 `B-ETF-01`，股票样本 `B-STOCK-01`。 | manifest 声明不要求凭据；具体 HTTP 服务授权/条款及两样本覆盖均未知。 | OHLC/量额字段、货币单位、调整能力、窗口、基准和可见时间均未知；事件时间不适用。 | 每个下列能力都绑定唯一实际函数/endpoint 后才可进入其独立探针；仍只有 Provider 级 dispatch 或 endpoint 变化即阻塞。关联 M21–M23；当前“选择阻塞”。 |
| `AK-STK-N` | `akshare/eastmoney` → `stock_zh_a_hist`，STOCK × DAILY_BAR × none；样本 `B-STOCK-01`。 | manifest 不要求凭据；服务授权、股票历史覆盖、页数/截断未知。 | 价格/量额币种及原始数量口径未知；请求窗口及数据修订未知；复权锚点不适用；事件时间不适用。 | BAR；证明明确 none 语义及实际字段单位才通过。空值/缺日、单位未知、未经授权或 endpoint 不同则阻塞。M21；当前“待执行，真实权限/覆盖未知”。 |
| `AK-STK-Q` | `akshare/eastmoney` → `stock_zh_a_hist`，STOCK × DAILY_BAR × qfq；样本 `B-STOCK-01`。 | 与 `AK-STK-N` 独立核验接口参数、可见权限和范围；当前均未知。 | qfq 锚点、算法、观察/修订时间、量是否复权均未知；不能继承 none 结果；事件有效/可见时间不适用。 | BAR；返回标记、基准与重叠窗口稳定性可解释才通过；只按参数名标 qfq 或锚点未知即阻塞。M21；当前“待执行”。 |
| `AK-STK-H` | `akshare/eastmoney` → `stock_zh_a_hist`，STOCK × DAILY_BAR × hfq；样本 `B-STOCK-01`。 | 同一 endpoint 但口径权限和覆盖独立记录，未知。 | hfq 锚点/范围及不同查询起点下重叠 Bar 是否变化未知；成交量调整口径未知；事件时间不适用。 | BAR；不同窗口结果一致，或窗口依赖有明确契约且被接受才通过；窗口敏感性不明即阻塞。M21；当前“待执行”。 |
| `AK-ETF-N` | `akshare/eastmoney` → `fund_etf_hist_em`，ETF × DAILY_BAR × none；样本 `B-ETF-01`。 | 目标拆分四日短窗曾返回；2026-09-29 宿主与目标容器对目标长窗均 `ConnectionError`，完整覆盖未知。 | OHLC/量额单位、币种字段、原始数量口径未知；none 语义仅按参数及短窗观察，事件时间不适用。 | BAR；精确 ETF 路由、字段和目标交易日完整才通过；股票覆盖不得外推到 ETF。M21；当前“短窗可读，长窗连接失败，准入待证”。 |
| `AK-ETF-Q` | `akshare/eastmoney` → `fund_etf_hist_em`，ETF × DAILY_BAR × qfq；样本 `B-ETF-01`。 | 2026-05-16..08-09 目标 59 个交易日已读到且短窗重叠 10/10 一致；更长范围及服务条款未核。 | 响应无量价单位/币种标注；qfq 基准/锚点/算法版本、量是否复权未知；不能从参数名推口径。 | BAR；仍须证明 qfq 语义、基准与字段单位才通过，不能据此与 HiThink 自动兼容。M21；当前“目标覆盖阶段通过，准入待证”。 |
| `AK-ETF-H` | `akshare/eastmoney` → `fund_etf_hist_em`，ETF × DAILY_BAR × hfq；样本 `B-ETF-01`。 | 目标拆分四日短窗曾返回；2026-09-29 宿主与目标容器对目标长窗均 `ConnectionError`，完整覆盖未知。 | hfq 基准、锚点、查询窗口关系和成交量口径均未知；事件时间不适用。 | BAR；分别完成窗口敏感性与字段单位断言才通过；覆盖或基准未知即阻塞。M21；当前“短窗可读，长窗连接失败，准入待证”。 |
| `AK-SPLIT` | AKShare `fund_cf_em` → ETF × SPLIT_EVENT；样本标的 `159516.SZ`，正样本事实引用 `E-SPLIT-01`。 | manifest 凭据声明不等于服务授权；endpoint、授权、拆分事件覆盖和完整范围未知。 | 折算日与实际交易生效日映射未知；比例单位、货币不适用；公告时间/Provider 首次可见时间未知。 | EVENT；先由外部权威事实锁定 E-SPLIT-01，再逐字段验证比例和生效日；折算日不得直接冒充生效日。正样本、权限或日期任一未知则阻塞。M22；当前“样本/覆盖选择阻塞”。 |
| `AK-CASH` | ETF × CASH_DISTRIBUTION；AKShare 分红 endpoint 当前未锁定；样本候选 `E-CASH-01`，目标覆盖另须 `B-ETF-01`。 | Token 未列为必需；实际 endpoint、许可、账户限制及 `510300.SH`/`159516.SZ` 覆盖未知。 | 现金金额/每份或每十份单位、登记/除息/支付日期、公告与首次可见时间未知。 | EVENT；先锁定单一函数与实际上游，检查 E-CASH-01 并分别报告目标 ETF 覆盖；接口/资产未固定、金额单位或日期不明即阻塞。M23、P02 R07.2；当前“endpoint 选择阻塞”。 |
| `AK-NOTICE` | `fund_announcement_dividend_em` → ETF × CORPORATE_ACTION_NOTICE；候选样本 `E-CASH-01` 的公开公告。 | AKShare 包装层的实际 HTTP 上游/许可、索引范围和公告时延均未知。 | 公告标题/发布时间、公告可见时间、抓取时间分列；公告索引没有事件比例/生效事实保证。 | EVENT；证明对应公开公告可以被定位且保留其源链接和可见时间才通过“公告索引”能力；不能因此通过 `AK-SPLIT`/`AK-CASH`。身份/许可/时间字段未知即阻塞。M23、P02 R07.4；当前“endpoint 与覆盖未知”。 |

`AK-ETF-Q` 阶段证据（2026-09-25）：以 AKShare 1.18.94 对 `159516` 两次只读调用 `fund_etf_hist_em(adjust="qfq")`。主窗 `2026-05-16..08-09` 返回 59 行，按目标窗口已核实的 59 个深市交易日逐日无缺失、重复或越界；短窗 `2026-07-27..08-09` 返回 10 行，10 个重叠日的共同字段值一致。规范化 DataFrame 的 SHA-256 分别为 `b8c5378adf5c7ea4cf2c46d97ad112fff029b4cce5d5f7f02195a0ac6c1bde2e` 与 `273f38ef0b0c97da1d9260ab436fd0ed30e15ef0b1d667ef519ebd1bed4fd2ae`，不是原始 HTTP 响应指纹。上游仍是 EastMoney；本次没有得到单位、复权算法/基准或量语义证据，因此 `AK-ETF-Q` 不能标已准入。

2026-09-29 `AK-ETF-N/H` 目标长窗与腾讯 `EX-TX-ETF-N` 的独立观察见主仓[只读核查证据](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-m2-public-source-window.md)。东财两口径在宿主和目标容器均连接失败，按预算停止；腾讯 raw 59 行和 10 行重叠稳定只提高了目标窗读取证据。深交所 2026 年 7 月官方 `159516` ETF 月度量额与腾讯 23 根日线的现有换算合计不一致，差异原因未知，不能据此授予原生单位或完整覆盖。许可、长历史和 `hfq` 缺口也继续开放。

### 3.2 Tushare Pro

Tushare 探针必须按 endpoint 分别报告 Token 对应账号是否有权调用、积分/套餐门槛、请求额度及错误码。Token 值绝不进入本文或证据。Spec 对 Tushare 日线声明的预期单位为成交量“手”、成交额“千元”；G0-M 必须核实最终选定 endpoint 的真实字段，确认后才分别按 100 股/手、1000 CNY/金额单位转换。端点/字段语义仍未知时不得先按预期值换算。

| 门禁 ID | P02 原子来源/能力与样本 | 权限与覆盖 | 单位、窗口/复权、事件时间 | 通过条件 / 阻塞条件 / 对应 M2 叶子 / 当前状态 |
| --- | --- | --- | --- | --- |
| `TS-ETF-BAR` | Tushare Pro / fund_daily × CN ETF × DAILY_BAR/1d/none。样本 `B-ETF-01`。 | 2026-09-29 宿主有效 Token 对 `159516.SZ` 精确短窗返回 `40203` 权限拒绝；积分/开通原因待用户账号侧核实。 | 官方接口量为手、金额为千元；本地按 100 份/手、1000 CNY 转换，日期分段与独立日历分别校验；修订绑定 Token/接入地址。 | 本地精确路由、SQLite 准入与 HTTP V3 闭环完成，真实权限/目标覆盖未通过。M24-b3；不得从 STOCK daily 权限外推 ETF。 |
| `TS-ETF-FACTOR` | Tushare Pro / fund_adj × CN ETF × ADJUSTMENT_FACTOR；样本 `159516.SZ` 覆盖 `B-ETF-01`。 | 2026-09-29 同一 Token 对目标精确短窗返回 `40203` 权限拒绝；不由股票日线权限推断。 | 原始交易日因子与观测/内容指纹已实现；锚点、首次可见时间、供应商修订未知，不将因子行当现金或拆分事件。 | M25-a 本地读取完成；M25-b 锚点/方向/冻结合同与转换关系、M25-c 真实权限/覆盖仍未完成。 |
| `TS-ETF-CASH` | Tushare Pro / fund_div × ETF 或净值基金 × CASH_DISTRIBUTION；候选 `E-CASH-01`，目标 `159516.SZ` 需另核原文真实性与覆盖。 | 2026-09-29 同一 Token 对 `510300.SH` 单基金返回 `40203` 权限拒绝，未获得分红行。 | 每份现金与独立源日期已标准化；CN ETF同完整代码/独立分红币种原字节及读前/读后准入、凭据、策略/安全配置复核本地通过，不扩展到OF/NAV。历史修订/首次可见时间未知，coverage=false。 | M26-a/b1及b2本地解析、映射读取、共享wire、Server在线/离线原字节消费与库存/HTTP已验证，见[生产接线](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m26-event-runtime.md)和[Server消费](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m26-identity-consumer.md)；URL [authority](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m26-url-authority.md)与[主机名](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-m26-url-host-proof.md)差异已窄修复。DSA[完整离线门禁](../../thesis-ledger/docs/tasks/evidence/2026-09-28-cont-dsa-stable-gates.md)两轮未通过，目标同步跳过；完整历史覆盖与M26-c独立公告/真实账号验收仍未通过。 |

逐接口真实权限及无凭据泄漏边界见主仓[2026-09-29 Tushare 核查](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-m2-tushare-token-permissions.md)。同一 Token 的 `000001.SZ` 股票 `daily` 单日成功，而上述三个基金接口各自返回权限拒绝；不把拒绝当空集或当前来源就绪。

### 3.3 官方 TdxAiData 与通达信运行条件

此组只针对官方 TdxAiData。官方身份、SDK/API 名称与版本、授权方式、支持操作系统/CPU 架构/运行时均未知；社区 Pytdx、现有通达信客户端和 a-stock-data 代码均不是官方 SDK 的替代证明。M27 的 P02 行仅声明 CN STOCK，故其股票样本不能作为 `159516.SZ` ETF 覆盖证据。

| 门禁 ID | P02 原子来源/能力与样本 | 权限与覆盖 | 单位、窗口/复权、事件时间 | 通过条件 / 阻塞条件 / 对应 M2 叶子 / 当前状态 |
| --- | --- | --- | --- | --- |
| `TDX-STK-N` | 官方 TdxAiData × CN STOCK × DAILY_BAR/none；样本 `B-STOCK-01`。 | 官方 SDK/API、授权/账户、调用额度和服务覆盖未知。 | 量价单位、币种字段、none 语义、历史范围、观察版本未知；事件时间不适用。 | 官方来源身份、权限、字段/单位、交易日覆盖和 raw 语义全部有证据才通过；不能导入/调用 SDK 前阻塞。M27、P02 R02.19–20；当前“官方身份/授权/覆盖未知”。 |
| `TDX-STK-Q` | 官方 TdxAiData × CN STOCK × DAILY_BAR/qfq；样本 `B-STOCK-01`。 | qfq 接口权限和样本覆盖独立核对，未知。 | qfq 锚点、调整算法、观察/修订时点、数量口径未知。 | 不从 none 推断 qfq；锁定返回标记、基准与窗口后才通过。M27、P02 R02.21–22；当前“口径未知”。 |
| `TDX-STK-H` | 官方 TdxAiData × CN STOCK × DAILY_BAR/hfq；样本 `B-STOCK-01`。 | hfq 权限与历史覆盖未知。 | 查询窗口可能影响 hfq 的风险来自 Spec；实际基准、量口径、修订时间仍未知。 | 用相同结束日、不同开始日的重叠窗口检查基准稳定性；不符合已记录契约或未知即阻塞。M27、P02 R02.23–24；当前“窗口/基准未知”。 |
| `TDX-RIGHTS` | 官方 TdxAiData × 单一资产待选 × CORPORATE_ACTION/RIGHTS；计划先用 `000001.SZ` 做来源/Consumer 选择，事件正样本与日期为**未知**。 | 官方事件接口、授权、资产范围和事件种类支持均未知。 | 权利/除权事件映射、比例/金额单位、生效日、公告日与 Provider 首次可见时间均未知。 | 固定 endpoint、一个既有 Consumer 和独立权威正样本后逐字段验证；身份、资产或样本未选前不发请求。不得把 ForwardFactor 当成逐日准确因子。M28、P02 R07.27；当前“选择阻塞”。 |
| `TDX-IMAGE` | **独立运行环境门禁**：固定官方 SDK/依赖版本，并在最终目标 ThesisLedger 镜像中验证；不通过宿主机安装或支持 Linux 的泛称替代。计划样本为镜像摘要、目标架构及上述 `B-STOCK-01` 单次只读请求。 | 授权/许可、二进制来源和镜像分发权未知。 | 核对 CPU 架构、OS/ABI、Python ABI、glibc/系统库、SDK 原生二进制、镜像平台与镜像摘要；事件时间不适用。 | SDK 可在目标镜像导入且一次只读请求成功、权限/许可明确才通过；只有宿主机可运行、镜像架构不匹配或需未授权系统依赖即阻塞。M34；当前“未核实”。 |

### 3.4 RQData

M30-b1 离线读取完成：分红 endpoint 与股票/拆分入口隔离，币种预检失败不请求来源，保留空响应版本，日期索引变化使版本变化；读取新增 11 项测试，基金事件相关共 66 项通过。该入口依赖调用方配置客户端，尚无真实账号或目标 ETF 准入证据。

2026-09-27 M30-a：基金 `fund.get_dividend` 使用 `dividend_before_tax` 每份税前金额，保留登记/除息/发放日；新增 19 项标准化测试，拆分与分红合计 55 项通过。[官方基金字段](https://assets.ricequant.com/doc/rqdata/python/fund-mod)。币种、ETF 映射、线上权限及完整历史未证明，`RQ-CASH` 仍未通过。

运行依赖核对：本地未安装 `rqdatac` 与 `rqdatac_fund`。只读检查 [rqdatac 3.7.1 发布包](https://pypi.org/project/rqdatac/3.7.1/) `client.py`，确认支持连接/读超时配置；客户端是模块级共享状态，实际接入须隔离账号初始化及凭据修订，不能直接在共享 Worker 中轮换全局账号。该检查没有安装依赖或登录账号。

读取接缝已增加单次 `fund.get_split` 查询和本地行数预算，包含日期索引的内容摘要覆盖空结果及日期修订，拒绝索引/日期字段冲突；36 项离线测试通过。SDK 内部传输超时和账号初始化尚未接线，调用预算回调只能拒绝晚到结果；此阶段仍不开放 Provider 路由或将历史覆盖标为完整。

2026-09-27 离线实现：新增 `rqdata_fund_splits.py`，按[官方基金接口](https://www.ricequant.com/doc/rqdata/python/fund-mod)的 `fund.get_split` 日期/比例合同标准化单基金响应。24 项测试及 flake8 通过；保留真实观测时间，不生成公告可见性，空集和非空集均不声明完整覆盖。调用方必须提供已核实的查询代码绑定；目标 ETF 对应关系、SDK/账号、在线读取和准入均未验证，`RQ-SPLIT` 不变为通过。

RQData 登录/账号授权、套餐/积分、接口允许的用途、请求上限和实际服务身份仍待真实核验。拆分、现金分红、历史 Bars 是三个独立能力；其中一个 endpoint 权限不得推断另一项。RQ 历史 Bar 已按[R02.17 候选核对](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-17-rqdata-etf-bar-selection.md)选定 `CN ETF × 1d × none`、通用 `get_price` 和现有 `MarketBarWindowReaderV3` Consumer；真实身份、原生单位、权限、覆盖与修订仍待探测。

| 门禁 ID | P02 原子来源/能力与样本 | 权限与覆盖 | 单位、窗口/复权、事件时间 | 通过条件 / 阻塞条件 / 对应 M2 叶子 / 当前状态 |
| --- | --- | --- | --- | --- |
| `RQ-SPLIT` | RQData / RQData API × SPLIT_EVENT；先选择资产，目标 ETF 样本候选 `159516.SZ`，正样本沿用 `E-SPLIT-01`。 | 登录/权限/额度、目标 ETF 支持与完整事件覆盖未知。 | Spec 候选字段含除权日和比例；比例单位、公告/可见时间和历史修订未知。 | 权威样本比例及有效日匹配，且样本覆盖边界明示才通过；事件 endpoint 未锁定、无可见时间却拟用于信号或资产不支持即阻塞。M29；当前“权限/覆盖/样本未知”。 |
| `RQ-CASH` | RQData / RQData API × CASH_DISTRIBUTION；目标覆盖 `159516.SZ`，日期语义正样本候选 `E-CASH-01`（若 endpoint 不覆盖该资产则阻塞，不默换标的）。 | 账户权限/额度及 ETF 支持未知，需单独验证。 | 登记、除息、派息日期分别留存；现金金额/币种/分母、公告和 Provider 首次可见时间、修订档案未知。 | 每种日期分别与独立公告核对，金额单位确定，并把 action fact 与 signal visibility 分开才通过；仅有生效事实不支持提前信号。M30；当前“权限/覆盖未知”。 |
| `RQ-HIST` | RQData 官方通用 `get_price` × CN ETF × `1d/none` 候选；现有 `MarketBarWindowReaderV3` Consumer；`159516.SZ` 是待核身份样本 `B-ETF-01`。 | 2026-09-29 目标 Control 只读状态为 `configured=false`、`credentialConfigured=false`、`capabilities={}`；真实行情权限/额度、ETF 覆盖与允许用途未知。 | 官方字段有 OHLC/volume/total_turnover，但本 ETF 的原生量额单位、原始价格基准、历史窗口完整性、修订与观察时点未知；qfq/hfq 另行选择。 | [R02.17 只读选择](../../thesis-ledger/docs/tasks/evidence/2026-09-29-cont-r02-17-rqdata-etf-bar-selection.md)和 R02.18 本地合同/隔离调用已完成，仍须真实账号、证券映射、单位、窗口及重叠差异探测；事件 API 权限不能替代 Bars。`G-M2-Price` 候选，当前“真实准入阻塞”，无正向能力。 |

### 3.5 已登记的现有 ETF 上游候选

只纳入 P02 中显式登记 ETF Bars、且可能参与目标 ETF `G-M2-Price` 选择的当前上游。若某个现有源只登记 STOCK，不能因 Provider 包含 ETF、库支持中国市场或股票接口成功，就提升成 `159516.SZ` ETF 能力。

| 门禁 ID | P02 原子来源/能力与样本 | 权限与覆盖 | 单位、窗口/复权、事件时间 | 通过条件 / 阻塞条件 / 对应 M2 叶子 / 当前状态 |
| --- | --- | --- | --- | --- |
| `EX-EFIN-ETF` | `efinance/eastmoney` × ETF × DAILY_BAR；调整维度未登记，样本 `B-ETF-01`。 | Efinance 函数到实际 HTTP endpoint 的映射、授权/服务范围和目标覆盖未知。 | OHLC/量额单位、none/qfq/hfq 具体支持及基准均未知；事件时间不适用。 | BAR；先锁函数实际请求和口径；如 fingerprint 落在 EastMoney，则只记录 EastMoney 的又一包装/调用入口，不增加独立来源数。`G-M2-Price` 候选；当前“source identity/adjustment 未知”。 |
| `EX-TX-ETF-N` | `tencent/tencent` × ETF × DAILY_BAR/none；样本 `B-ETF-01`。 | 精确 `newfqkline/get` 已在 `159516.SZ` 宿主及目标容器返回目标 59 日窗，短窗 10/10 重叠一致；使用条款、完整历史范围与目标准入未知。 | 本地按锁定版 AKShare 的手/万元声明换算，真实原生单位、币种及版本仍待独立核验；事件时间不适用。 | BAR；完成独立 source fingerprint、ETF 覆盖和 raw 单位核对才进入备选；HFQ 独立见 `EX-TX-ETF-H`。`G-M2-Price` 候选；当前“目标窗可读，G0-M 未通过”。 |
| `EX-TX-ETF-Q` | `tencent/tencent` × ETF × DAILY_BAR/qfq；样本 `B-ETF-01`。 | 同一腾讯上游的 qfq 短窗可读；完整覆盖、实际授权/条款未知。 | 拆分事件窗 qfq 历史价格与 raw 不同，响应量额一致；锚点与长期窗口仍未知。 | 只允许将已验证的 qfq 用作相应 qfq 能力，不转换成 raw/hfq；目标 M2 fallback 若要求 raw/hfq 则不匹配。`G-M2-Price` 候选；当前“短窗可读，G0-M 未通过”。 |
| `EX-TX-ETF-H` | `tencent/tencent` × ETF × DAILY_BAR/hfq；样本 `B-ETF-01`。 | 159516.SZ、2026-04-30..08-09 原生 `hfqday` 完整 68 日、短窗重叠 10 日一致；正式 Data V3 返回 68 行。 | 限上述标的、窗口和固定快照纯价格研究，准入至北京时间 2026-10-10 01:17:22；量额单位及高级语义保持未知，不借用 none/qfq 准入。 | 目录 ready、policy 33 对齐；Server 准备因缺日级元数据 blocked，Run/重放未通过。完整 `G-M2-Price` 与通用 G0-M 仍开放。 |
| `EX-YF-ETF` | `yfinance/yfinance` × ETF × DAILY_BAR/`auto_adjust=true`；样本 `159516.SZ`。 | P02 登记的市场范围含 CN 标签，但此标的/ETF 可用性、条款、访问权限和历史范围未知。 | `auto_adjust` 不能直接等同 canonical qfq/hfq 或 raw；币种、成交量单位、历史版本未知；事件时间不适用。 | 只有在目标 ETF 实际覆盖、允许用途且算法/基准能映射到精确所需口径时才作为候选；映射未知即阻塞，不记为 raw/hfq 备用。`G-M2-Price` 候选；当前“目标覆盖/口径未知”。 |

#### 已登记但不能作为目标 ETF 独立备源的现有行

以下 P02 行仍保留在能力目录；它们只登记 CN STOCK，或缺少与 M2 ETF 相符的原子能力，因此不为 G0-M-ETF 创建请求。若需要把它们纳入 M2 股票能力，应先有精确 M2 叶子、样本和 Consumer，再新增对应来源门禁，不能将这份 ETF 证据入口外推。

| 已登记原子行 | 当前可用于 M2 `159516.SZ` 的判断 | 不计独立来源的原因 / 下一步 |
| --- | --- | --- |
| `akshare/sina × STOCK × DAILY_BAR × none/qfq/hfq` | 当前不适用 | P02 未登记 ETF Bars；不得以股票覆盖推断 ETF。 |
| `akshare/tencent × STOCK × DAILY_BAR × none/qfq/hfq` | 当前不适用 | P02 未登记 ETF Bars；与 `tencent/tencent` 腾讯上游先去重。 |
| `tushare/tushare × STOCK × DAILY_BAR` | 当前不适用 | ETF fund_daily 已独立登记并经 V3 精确入口读取；其真实权限不能从 STOCK daily 外推。 |
| `tickflow/tickflow × CN STOCK × DAILY_BAR` | 当前不适用 | P02 只登记 STOCK，套餐、单位和复权也未知。 |
| `pytdx/pytdx × CN STOCK × DAILY_BAR` | 当前不适用 | 仅股票；是社区行情服务器协议，不等于官方 TdxAiData，也不自动证明实际行情上游独立。 |
| `baostock/baostock × CN STOCK × DAILY_BAR/native default adjustflag=2` | 当前不适用 | P02 仅登记股票并声明默认前复权；不外推 ETF/hfq。 |

### 3.6 不纳入本门禁的来源分支

- `a-stock-data` 的 Tencent、通达信盘后包和公告候选均属 P02/G0-R；先完成固定版本、代码/许可和实际 endpoint 身份审核。其代码名称不生成新 Provider，也不能绕过本文件的来源去重。
- `free-stockdb` 没有可信镜像地址、数据来源链和许可证据；按 P02 R08.1/G0-R 独立准入。镜像地址、来源、许可、可达性、覆盖边界任一未知都保持关闭。它不属于 M2 Provider 证据，也不能替换 PostgreSQL 或冻结快照。
- 官方 TdxAiData 的 SDK、CPU/OS/ABI 与目标镜像检查已单独列为 `TDX-IMAGE`/M34。不得用宿主机成功或“支持 Linux”宣称目标镜像可运行。
- HiThink 基金分红对应 M31/G0-H；G0-M 只引用既有 HiThink 证据边界，不重复请求或将其 qfq ETF 序列当成 M2 raw/hfq 独立备用。

## 4. 独立性与 M2 覆盖关系

| 实际上游关系 | 暂定计数 | 解除同源/能力阻塞的证据 |
| --- | --- | --- |
| AKShare/EastMoney 与 Efinance/EastMoney | 同一候选来源组，最多计 1 个 | 实际 host/path/协议、响应字段指纹和调用链证明为不同上游；包装库、Provider ID 或 sourceId 不足以证明独立。 |
| AKShare/Tencent 与 TencentFetcher/腾讯直连 | 同一候选来源组，最多计 1 个 | 上游服务身份及实际请求/响应指纹确认不同。 |
| 官方 TdxAiData 与社区 Pytdx | 协议/供应者身份分别登记；下游行情可能同源，独立性未知 | 官方服务身份、数据来源说明或足够的响应/服务标识证据；SDK 不同不足以判独立。Pytdx 当前又无目标 ETF 原子行。 |
| Tushare、RQData、Tencent、EastMoney | 各自保留 Provider 与 upstream key，当前独立性/目标能力仍未知 | 各自授权成功、实际 endpoint 指纹与 `159516.SZ` 目标能力通过；仅不同注册名不能证明数据链独立。 |

M2 子叶覆盖映射：

- `M21` ← `AK-STK-*`、`AK-ETF-*`；每个 adjustment 是独立探针结论。
- `M22` ← `AK-SPLIT`；拆分正样本必须先由外部权威来源选定。
- `M23` ← `AK-CASH`、`AK-NOTICE`；事件事实、公告索引和可见时间分别判断。
- `M24` ← `TS-ETF-BAR`；`M25` ← `TS-ETF-FACTOR`；`M26` ← `TS-ETF-CASH`。
- `M27` ← `TDX-STK-N/Q/H`；只证明 CN STOCK，不满足目标 ETF 覆盖。`M28` ← `TDX-RIGHTS`；`M34` ← `TDX-IMAGE`。
- `M29` ← `RQ-SPLIT`；`M30` ← `RQ-CASH`；`G-M2-Price` 候选 ← `RQ-HIST` 和 `EX-*`。候选并非已选来源，最终 raw/hfq 路由仍由 `G-M2-Price` 实测决定。
- `M31` 由 G0-H 管理；`M32/M33` 是下游转换/消费叶，不由来源准入表单代替。

目前没有证据证明任一新增源已具有 `159516.SZ` 目标 raw 或 hfq 完整能力，也没有可计数的跨独立上游 M2 备用路径。以本计划本身不能勾选任何 M2 实现或真实运行验收项。

## 5. 证据入口与逐门禁记录模板

每个门禁 ID 单独保存一份结论摘要，建议路径：`docs/evidence/thesis-ledger-m2-g0/<门禁 ID>/<YYYY-MM-DD>.md`。先确认 DSA 仓库证据目录约定和数据/许可保留规则；如不允许保存响应，仓库仅存脱敏元数据及哈希，原始样本留在受控环境并记录其访问位置。本文作为清单索引，不在仓库提交原始响应、Token、Cookie、会话或整段运行日志。

```text
门禁 ID / P02 原子行：
执行时间、操作者、DSA commit、Spec/Task commit：
Provider / SDK / manifest 版本：
实际服务身份与 endpoint 模板（去除凭据及敏感 query）：
授权状态（仅 yes/no/unknown）、接口权限/套餐/配额分类：
资产、单能力、复权/周期；精确样本与时区边界：
请求窗口、分页/限额、预热范围、交易日历版本：
HTTP/业务状态分类、返回行数、唯一日期数、边界/缺口/重复：
脱敏响应 SHA-256 / Source fingerprint：
OHLC、币种、成交量/成交额单位、数量/量复权口径：
调整方法、锚点/范围、观察时间、修订指纹、重叠窗口结果：
事件类型/比例/现金单位；登记/有效/支付/公告/首次可见/抓取时间：
错误断言及结果（权限拒绝、unsupported、empty、限流、网络、解析分别记录）：
通过范围 / 未知项 / 阻塞原因：
判定：通过 | 阻塞 | 不适用；对应 M2 叶子：
```

证据摘要不得只写“请求成功”。完整 URL 若含 Token/签名参数须先脱敏；日志只记错误类别、HTTP/业务状态、endpoint 模板和请求指纹，不保存响应中的账户或个人信息。没有独立公告支持的事件时间、没有精确目标样本的权限、没有 source fingerprint 的独立性一律保留未知。

## 6. 完成判定

### M24 本地读取进展（2026-09-27）

已将 ETF `fund_daily` 读取接入独立的有界日期分段模块；每段最多 366 个日历日，默认最多 32 次、硬上限 64 次，预算不足在发请求前拒绝。逐段保留窗口/行数/摘要，拒绝错标的、范围外数据、重复日期、缺关键列和无效量价。保留原有手到份、千元到元换算，新增测试确认数值字符串不会变成字符串重复。

依据 [Tushare fund_daily 官方文档](https://tushare.pro/document/2?doc_id=127)，当前接口每次最多 5000 行，vol/amount 分别按手/千元返回；采用日期分段，无需假设未列明的 offset 协议。接口页当前权限描述与旧总览不同，不能从旧积分阈值推断实际可用性，真实权限仍按该接口单独验证。

M24-a 阶段新增 17 项、既有获取/HTTP/限流回归 34 项，共 51 项通过，2 项既有弃用警告。读取结果明确 `tradingCalendarVerified=false`；空数据和分段取完都不授予完整行情覆盖。后续 M24-b2 已完成精确库存、凭据修订和独立日历覆盖的本地接线，见本文件 §2；实际来源准入和目标覆盖仍未完成，没有调用真实 Provider 或更新容器。

G0-M 计划完整性只在以下条件全部满足后由主仓 Task 的执行所有者核对：M21–M30 所列来源能力有独立入口；每个实际探针单元都具有自己的样本、权限、覆盖、量价/货币单位、窗口/基准/复权、事件有效/可见时间、错误断言、通过/阻塞条件和 M2 叶子映射；TdxAiData SDK/架构/镜像为独立 M34 门禁；同源包装不会重复计作备用；未选 endpoint/未选事件样本/未授权账户保留阻塞状态。

G0-M 计划完整不等于真实 G0 通过。只有实际执行并留下脱敏证据的原子单元可以标记“通过”；缺少授权、源身份、目标覆盖、单位、复权基准或时间语义时标记“阻塞”或“未知”。本文件创建时所有真实 Provider 状态均为“未执行”；不将规划、源码声明、静态 manifest 或此前其他门禁的响应结果升格为 M2 来源就绪证据。

## 7. M31 HiThink 基金分红离线映射（2026-09-28）

依据 [HiThink 官方基金分红端点](https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/fund/corporate-actions-dividends.md)，`data.item[]` 的税前/税后金额是每 10 份现金，日期字段是 Unix 毫秒。`data_provider/hithink_fund_dividends.py` 只处理已取得的 `item[]`：金额除以 10 得到元/份，按上海当地日期分开保留公告、登记、除息、发放、再投资和基准日期。仅 `progress="实施"` 且金额与日期有效的记录生成现金事实，抓取时刻仍是实际观测时间；未实施、空响应或窗口过滤不会变成完整覆盖。定向及相邻分红测试共 60 项通过。

此模块不发起 HiThink 请求，不核验基金币种或目标身份，不签发历史事件覆盖及策略可见时间。精确端点读取、真实权限、目标准入和冻结消费归 M31-b；价格量额单位与 `159516.SZ` 普通回测门禁不因分红字段映射而改变。

M31-b1 另增加 `data_provider/hithink_fund_dividend_reader.py` 的单标有界传输。它向官方分红端点传一个完整 `thscode`；2026-09-29 按当前官方请求合同再要求调用方显式传 `fund_type`，只允许 `exchange` 对应 `.SH/.SZ`、`otc` 对应 `.OF`，拒绝从后缀静默猜基金类型或在此路径启用 REITs。API Key 仅在请求头，禁重定向、无隐式重试；默认响应上限 1 MiB/2000 条，逐字节摘要及实际观测时间随返回保存。HTTP/业务错误、重复 JSON 键、超限及返回计数矛盾失败关闭；缺少 `dividend_count` 时明确不确认传输计数。此前新增读取与标准化接缝 16 项、连同标准化/相邻分红合计 76 项离线测试通过；后续显式基金类型回归单独记录。此读取器尚未接入实际 V3 事件路径，返回的 `historyComplete=false`，也未核验真实账号权限或历史覆盖。

真实 `510300.SH` 只读响应两次均返回 14 条、计数相符，但 14 条的 `progress` 均为字符串 `"2"`，与官方示例 `"实施"` 不同。单条已核对分红的日期与金额虽吻合基金管理人公告，仍无法据此定义所有数字进度码。标准化器对未核实进度值现在明确报错，避免将真实已发生事件静默变成空事实；M31-a 的完成标记已撤回。当前相邻测试 77 项通过；M31-b1 的传输结果不能授予历史完整覆盖或事件 V3 准入。真实探针摘要和请求预算见主仓 `docs/tasks/evidence/2026-09-28-cont-m31-live-progress-drift.md`。

2026-09-29 的 M31-b2 前置合同新增 `thesis_ledger_hithink_dividend_contract_v3.py`：仅识别 `CN/ETF/CASH_DISTRIBUTION` 与 `hithink/fund-corporate-actions-dividends`，凭据修订必须是当前 HMAC 摘要格式，适配/来源修订各自固定。13 项定向、flake8 与编译检查通过。此阶段尚未开放事件库存；后续接线与当前状态见下文。未知 `progress="2"` 和历史覆盖不完整继续阻断真实准入。

同日新增 `hithink_fund_identity_evidence.py`，只校验已审定的独立 ETF 身份/分红币种原文：`hithink-fund-identity` 版本 1 包含完整查询代码、`exchange` 类型、日期范围、核验观察时刻，以及两类 HTTPS 文档地址和 SHA-256；当前精确准入的 `evidenceRef/evidenceSha256` 绑定原始 UTF-8 字节。重复 JSON 字段、重复证券、错误身份或币种、越界或晚于冻结时刻的证据一律拒绝。此模块的 21 项定向测试只使用合成文件；没有获取或审核真实身份/币种文件，也没有运行时当前凭据复核、事件库存登记、目标部署或完整历史覆盖。

随后新增 `thesis_ledger_hithink_mapped_dividend_read.py`：先按当前准入读取并核对内容寻址原文，再固定环境 API Key/主密钥 HMAC 快照调用有界单次读取器；读后重新读取准入、原文和凭据，撤销、轮换或超出冻结时刻均拒绝。严格标准化仍拒绝未知 `progress="2"`，结果保留响应指纹和 `complete=false`。10 项合成正反例（含真实有界读取器的注入 HTTP 组合）及限定 lint/编译通过；后续事件入口与 Server 校验已接线，真实文件审核仍待实施。

官方页面现在要求 `fund_type`，因此仅新增一次显式 `fund_type=exchange` 的目标基金只读对照；`510300.SH` 仍返回 14 条、进度均为字符串 `"2"`。这排除了“省略基金类型导致数字进度码”的单一猜测，没有建立数字码字典；响应摘要与请求预算详见主仓真实字段差异证据。读取器已改为显式参数并完成本地正反例；手工探针不作为生产路径通过。

M31-b2 本地接线将精确来源同时登记进事件库存、HiThink manifest 和事件执行入口。Control 与目录只在当前准入、环境 Key 的 HMAC 修订、内容寻址的 ETF 身份/分红币种原文和全部准入标的范围成立时报告可用；执行请求先核身份原文，再核鉴权/策略，读取时固定凭据快照，返回前复核撤销、轮换、原文和策略。响应保留实际 `hithink` 来源、原文证据及 `complete=false`。合成 SQLite/HTTP 定向和 DSA→Schemas/Server 协议验证通过；隔离 DSA 官方离线门禁 7527 项通过。目标容器经 `sync-code.sh all` 更新后均 healthy，目标 DSA 目录中的精确分红条目仍为 `not_admitted`，鉴权 HTTP 请求返回 422 / `not_admitted`。上交所公告支持的 `510300.SH` 身份/币种候选原文已另行核对，但未写目标 Control 或签发准入；本次仅代码同步，镜像未更新。详细测试与目标拒绝路径见主仓 M31-b2 接线证据；这一步不签发真实事件准入或完整历史覆盖。
