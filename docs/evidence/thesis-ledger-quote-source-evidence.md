# 报价来源选择与验收证据

本文件记录 M3 R02 报价叶子的源码和运行证据；唯一能力登记仍为 `thesis-ledger-source-capabilities.md`。

## 成交量口径缺口

腾讯 `_normalize_tencent_volume` 使用价格、换手率和流通市值估计成交量，再比较原值与乘以 100 的差异；无法比较时默认乘以 100。该实现属于数值启发式，不能独立证明来源字段单位。已有两组样本测试证明当前算法行为，不是官方单位或全范围验收，R02.5 的单位门禁保持开放。

Efinance 0.5.9 的实际 `get_quote_snapshot` 来自 `https://hsmarketwg.eastmoney.com/api/SHSZQuoteSnapshot`，将 `volume/amount` 直接重命名为成交量/成交额并转数值，未在此层执行单位转换。目标函数源码 SHA-256 为 `b67e0f4165da3e715853a8fd75e2b5b38855c9dc5d001147f5f0248dc8b2a7c4`。[官方 API 文档](https://efinance.readthedocs.io/en/latest/api.html#efinance.stock.get_quote_snapshot)提供字段及示例，但本次核查未取得足以覆盖股票与 ETF 的显式单位约定。不能根据示例数值比例签发单位证明，R02.7/R02.8 仍开放。

本次同时修复 Efinance 单标快照的身份缺口：返回必须为单行 DataFrame 或 Series 且包含与请求匹配的代码，不能接受缺代码或把多行首行当作单标响应。该修复不改变数值换算，不将身份通过视为单位通过。

快照适配及请求预算相关 13 项测试通过，关键错误 flake8 通过。没有重复请求已用尽预算的 Efinance 全市场目录接口，单标快照是不同 endpoint 和独立消费范围。

官方同步成功，日志 `/private/tmp/goal-efinance-identity-sync-20260927.log`，目标 EfinanceFetcher 与宿主 SHA-256 同为 `6935cc12058e0ba5a1a202bc9ef452bdc931ad81652150a41edcf05d8ecd08a4`，新源码位于容器可写层。目标直接调用 `_get_realtime_snapshot_quote` 的独立探针 `/private/tmp/goal-efinance-snapshot-probe-20260927.py`，股票 600519 与 ETF 159516 均首次成功，分别返回贵州茅台及半导体设备ETF国泰，代码匹配且量额字段存在，无需重试。每个标的单轮硬预算 45 秒，未写业务数据库或准入。此证据证明两类各一个单标快照适配可执行；量额存在不证明单位，未扩大为全市场覆盖或回测日线权限。

## 腾讯入口核对

2026-09-27 核对宿主 `data_provider/tencent_fetcher.py` 与目标容器实际类：`TencentFetcher` 继承 `BaseFetcher`，未提供 `get_realtime_quote`，目标反射 `callable(getattr(TencentFetcher, "get_realtime_quote", None))` 为 false。其端点为 `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`，能力是日线，不作为报价证据。

已有实时报价位于 `AkshareFetcher._get_stock_realtime_quote_tencent`，常量指向 `qt.gtimg.cn/q`，由 `DataFetcherManager.get_realtime_quote` 的腾讯分支调用 `AkshareFetcher.get_realtime_quote(source="tencent")`。这证明现有消费路径存在，未执行网络请求，未证明时间、单位或覆盖门禁。

因此 R02.9 的直接 TencentFetcher 报价候选为 unsupported，R02.10 跳过该组合实现；既有腾讯报价的来源适配继续在 R02.5 中核实，避免并行 API 或重复计算独立备用来源。专题无对应英文版。

## 腾讯报价身份校验合同

现有解析只取首尾引号之间内容，随后把请求代码写入统一报价，缺少返回身份核对。修复要求：响应必须是唯一的 `v_<请求市场及代码>="字段";` 赋值，正文代码也必须与请求一致，至少 45 个字段且名称非空；多条赋值、错市场、错代码和额外脚本整体拒绝，不执行响应文本。只有成功构造报价后才记录来源成功。时间、成交量单位与延迟门禁仍需独立核实，不由身份校验通过外推。

本地实现：纯校验位于 `data_provider/tencent_quote_identity.py`，由现有 AKShare 腾讯方法调用，旧解析代码移除，原有大文件未增加规模。错市场/正文错码/空名/重复响应/附加脚本/短字段/非 ASCII 身份和构造失败均返回不可用且不记录成功。既有成交量样本原先请求 688691 却返回 601006，已修正样本身份以保持成交量验证目的。新增测试、既有报价日志与回退测试共 24 项通过，关键错误 flake8 通过。

目标验证：官方代码同步完成且目标健康，日志 `/private/tmp/goal-tencent-identity-sync-20260927.log`，镜像未变，新源码位于容器可写层。宿主与目标 helper SHA-256 均为 `133847c4206fe00d6de5686239d16afbeec0bfb574e448415d763c9e081f7b0e`，AkshareFetcher 均为 `89a0b9f2d517e97e198f3e858ab5da221120d8889d9ae061938e04044142d4aa`。

真实只读探针 `/private/tmp/goal-tencent-quote-probe-20260927.py` 单轮硬预算 30 秒，首次返回 `600519 / 贵州茅台`，价格字段存在、来源为 TENCENT，通过上述双重身份校验，无需重试。未写业务状态或准入；返回价格存在不证明报价新鲜、当日开市或成交量口径，R02.5 时间/单位及完整范围门禁继续开放。

## 腾讯来源时间传递

时间消费核对发现统一报价已有 `provider_timestamp`，Manager 会按该时间计算 `stale_seconds/is_stale`，但腾讯适配未赋值响应字段 30。本次以严格十四位 `YYYYMMDDHHmmss` 解析该字段，附加 `Asia/Shanghai` 时区；格式或日期无效返回未知，不以本地获取时间替代来源时间。没有改变 Manager 的既有 TTL 策略或日历语义。

新增非法日期、缺失、截短、非 ASCII、错误分钟、伪时区后缀以及实际适配输出测试；Manager 测试确认 2026-09-25 15:00 中国时间在 2026-09-27 07:00 UTC 获取时仍保留原始时刻，过期秒数为 172800。与身份、日志和回退测试共 33 项通过，关键错误 flake8 通过。目标时间字段使用下述独立探针验收，先前的身份探针不自动覆盖本次时间修复。

时间修复目标验收已完成：官方同步日志 `/private/tmp/goal-tencent-time-sync-20260927.log`，目标健康，镜像不变。宿主与目标 helper 摘要为 `35266b0ec3b008ab999a4322c318c60fd8a919fb8f541337755596de97ccce08`，AkshareFetcher 为 `bcee29f4a1b5e94830dcba174b4a7a982695be43a79469d67946f165c390d100`，替代前次身份修复摘要。更新在容器可写层。

实际探针 `/private/tmp/goal-tencent-time-probe-20260927.py` 首次通过：600519 来源时间为 `2026-09-24T16:14:44+08:00`，Manager 标准化为 `2026-09-24T08:14:44+00:00`，获取时间为 `2026-09-27T03:43:02.917837+00:00`，`stale_seconds=242898`、`is_stale=true`。这确认返回旧报价时来源时点不会被获取时点替代；不解释上游为何返回旧值，也不证明交易所实时性。单轮硬预算 30 秒，无需重试、无业务写入。成交量单位、完整范围与最终 API 消费门禁继续开放。

## API 新鲜度消费修复

对应主仓 `2026-09-27-quote-freshness` Spec/Task。网关直用适配器，不能依赖 Manager 已填 is_stale；API 现按响应生成时刻检查来源时间，超过默认 600 秒为 stale，来源明确过期不降级。缺失、非法、无时区或未来时间为 unknown，合法未来时刻仍保留用于诊断；近期合法来源时间沿用现有 live 枚举。unknown 时的旧协议 marketTime 回退不代表来源时间已知。

时间边界、绕过 Manager 的实际 `_real_quote` 序列化、腾讯适配与网关相关 35 项测试通过，关键错误 flake8 通过；原 API 大文件通过提取原判定逻辑减少规模。序列化测试随后提升为带鉴权的实际 FastAPI 路由调用，专项 11 项再次通过，旧报价仍返回 stale。

目标官方同步完成，日志 `/private/tmp/goal-quote-api-time-sync-20260927.log`，健康、镜像未变。宿主与目标 `api/thesis_ledger.py` 摘要为 `235d2f53a0593fef4ac0b371501fa37131498900e2c1d99a34d801f74f811dfb`，新时间模块为 `a6b76490b9d0ea471f9c92efa022407d54fa471e53303d9c4a01f3e758c9ebe1`；源码位于容器可写层。

目标真实 HTTP 探针 `/private/tmp/goal-quote-api-probe-20260927.py` 首次返回 200，标的 600519.SH，provider=efinance、upstreamSource=eastmoney、fallbackUsed=true。返回 `marketTime=fetchedAt=2026-09-27T03:48:53.570828+00:00`，freshness=unknown、stale=false：来源时间缺失时沿用兼容回退但不标为 live。无需重试，凭据只在容器进程内读取，未打印。此请求没有修改路由或准入，但可正常产生服务诊断与 Provider 请求计数；不作为无副作用探针。旧报价分支由本地实际 HTTP 受控来源测试覆盖，本次目标并未选中腾讯。报价时间修复验收完成，R02.5 整体仍缺单位与完整范围验收。
