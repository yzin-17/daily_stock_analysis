# 独立数据环境及接口矩阵验收

对应原 Task 的 DG1a、DG1b、DG1c。验收输入是当前工作区的数据实现、D0 类别清单及已有数据断言；来源数据、凭据和原件均为本地受控夹具。没有操作目标容器或原业务数据库。

## 物理环境与入口

`tests/data_runtime_environment.py` 从数据入口、Provider、服务及其本地导入闭包推导发行包，再递归解析当前安装版本的依赖。新建不含 pip、关闭系统及用户 site 的虚拟环境，逐发行包复制原文件，保留版本、元数据和源码字节。原虚拟环境未修改；没有安装或升级外部包。

数据闭包为 97 个发行包；矩阵环境另含 pytest 等 4 个测试工具，共 101 包、9,264 份常规文件。测试环境清单 SHA-256 为 `61c2cb54a151a951109156e1450bb3363e6e3933b2bf71d2e5fb8d1363ff00e6`。清单及完整日志位于本轮自有临时目录，不进入生产依赖清单。

| 数据模块 / 发行包 | 保留版本 |
| --- | --- |
| akshare | 1.18.94 |
| baostock | 0.9.3 |
| efinance | 0.5.9 |
| futu-api | 10.8.6808 |
| longbridge | 4.5.0 |
| pytdx | 1.72 |
| requests | 2.34.2 |
| google-search-results / serpapi | 2.4.2 |
| tavily-python / tavily | 0.8.1 |
| tickflow | 0.1.25 |
| tushare | 1.4.29 |
| yfinance | 1.7.0 |
| exchange-calendars | 4.13.2 |

原环境可见的 12 个数据 SDK 模块均保留。`openai`、`anthropic`、`litellm`、`google.genai`、`google.generativeai`、`langchain`、`llama_index` 均物理不可见，对应发行包也不在环境内。每次独立服务启动都重新检查这两组事实。D9a 扩大字面量动态导入检查后，另确认原环境缺少 rqdatac，装配继续保留缺失并输出清单；见[打包补充证据](2026-10-19-data-runtime-packaging.md)。

`tests/data_runtime_process.py` 在新解释器中实际导入 `server:app`，运行 Uvicorn、本地 TCP、正式生命周期和数据配置。只接受本地网络连接；使用空 ENV_FILE、受控 Token 和临时数据库/日志。冷启动测试核对完整 OpenAPI 路由、Data/Control 鉴权、31,678 行索引及旧 V1/V2 各方法的 404；在同一临时数据库上启动和停止两次。退出时检查子进程归零、生命周期及 OAuth 状态移除、产品配置/分析/Agent/通知/模型模块未导入。

## 类别与证据层

完整矩阵在独立的物理数据解释器中运行 249 份数据测试。它包括真实 Provider 工厂、Registry、DTO、临时 SQLite、固定原件、解析、SDK 接缝及 ASGI 路由；没有把整个矩阵表述为真实供应商或目标环境验收。另由真实 `server:app` TCP 服务覆盖各类纵向路径，并由主仓消费者检查响应及原文关联。

| D0 类别 | 数据矩阵中的现有责任 | 独立 TCP / 主仓读取 |
| --- | --- | --- |
| 来源与精确绑定、报价 | 所有已注册来源的清单、工厂、权限/配置、价格/数量未知与真实零、单位/时间、主备及撤销 | 报价生产与主仓 MarketQuoteReader 的实际 HTTP 证据见主仓 G1；本矩阵在物理数据环境重跑相应报价与绑定断言 |
| 完整窗口、图表 K 线与指标 | 精确窗口、会话日历、复权、原生单位、分页传输、截断、指纹、预热不足 | 真实 Runtime 接收带完整传输摘要的原生 Frame，由生产模块生成 bars/chart DTO；主仓两个 V3 客户端解析。指标完整及不足输入、指纹拒绝均走真实 HTTP |
| 事件 | 已准入范围、源记录、空结果不代表完整覆盖、晚到撤销、预算 | 实际事件模块生成有界空结果，coverage.complete 保持 false；主仓事件交换解析通过，越界请求拒绝 |
| 日历 | 来源发布版本/字节、日期上下界、请求范围和历史可用时间 | 真实已核验 exchange-calendars 包，经 market/backtest 两种日历接口和主仓 Schema 读取 |
| NAV | 普通 NAV/历史截窗及分页校验；精确 NAV 的原页、身份、规则、披露、预热/尾部和哈希 | 精确 NAV 用受控原始分页/身份数据及实际日历包生成完整原文；DsaNavClient 严格核对原页、规则、身份和日期，strict-publication 仍拒绝 |
| 规则和参考资料 | 公共费率及市场差异、静态资料/每手/状态合同、精确整数、未知、当前资料不冒充历史完整 | 生产公共收费包经真实 Control/Runtime/HTTP 及主仓客户端读取；超覆盖拒绝。Longbridge 工厂接收受控 SDK 静态对象，由正式每手投影和主仓读取函数生成/验证响应 |
| FX | 原表副本重开、直接/反向、同币种、日期上界、过期/缺失、无时区不补 fetchedAt | 实际 FX 仓储和服务读取受控旧表数据，主仓 FX Schema 解析 |
| 控制、OAuth、目录 | 真实临时存储、加密快照/引用、修订、lease、预算、配置轮换、恢复、重复启停与关闭 | 正式生命周期实际创建并释放这些资源；冷启动逐路由鉴权，数据矩阵覆盖对应成功及失败状态机 |
| 公告原件和任务 | 原文固定副本、哈希/引用、获取/解析区分、lease/attempt/owner 原子终态与恢复 | 公告真实生命周期启动/退出；数据矩阵保留解析、原件及任务恢复断言 |
| 索引、搜索、新闻 | 数据种子、缓存/下载失败恢复；现有搜索适配器与 N1～N3 生产语义 | 冷启动实际读取独立数据索引。新闻独立 Uvicorn HTTP 测试在物理环境重跑，覆盖成功、空、部分、原文不可信指令、未知时间、去重、截断与撤销零尝试 |

K 线、精确 NAV 和事件的独立进程场景复用原有受控输入，在 SDK/原始记录边界替换数据；最终 API DTO、指纹、日历和校验由真实生产模块生成。公共规则直接读取仓库规则包。它们没有取得或模拟真实来源准入资格。

## 命令与结果

DSA 根目录：

```sh
.venv/bin/python -m tests.run_data_runtime_matrix
```

该入口创建隔离环境、关闭 pytest 外部插件自动加载、使用独立 HOME/数据库并阻断非本地连接，保留完整日志和逐测试 XML。最后完整运行：**4,184 passed、1 skipped、0 failed / error**，249 文件，145.39 秒。日志根目录为 `/private/var/folders/t6/qhk7wqzd07g25xwy7mr46gb00000gn/T/dsa-data-runtime-8gxnj1lj`。随后对新增静态工厂安全断言和最终独立进程模块定向重跑：**14 passed**，日志根目录 `dsa-data-runtime-eb7p59sc`。两组有重叠，不相加。

唯一 skip 是 `test_provider_oauth_native_storage::test_native_builder_loads_encrypted_snapshot_without_authorization`：当前原环境没有该测试要求的定制 Longbridge SDK wheel。普通 longbridge 包实际保留且可见；定制原生绑定仍由 DG2a 独立验收。

主仓根目录，使用装配后包含 pytest 的解释器：

```sh
DSA_DATA_RUNTIME_PYTHON=<隔离环境>/python/bin/python \
  pnpm --filter @thesis-ledger/server exec vitest run test/dsa-data-runtime.integration.test.ts
```

**6 passed**。真实 DsaClient、DsaNavClient 和每手读取函数消费独立 DSA TCP 响应，正常及拒绝分支均通过。常规主仓单元测试未设置该变量时不计入这项跨仓验收。

其他验证：原环境配置/指标迁移 oracle、分离后的流水线断言及名称预取 **53 passed**；新 Python 验收模块完整 flake8；DSA `ci_gate.sh syntax / flake8 / deterministic`、主仓 Server typecheck 和该文件 ESLint 通过。旧产品 oracle 与 Agent 工具测试继续保留，不把其未纳入数据解释器说成删除了断言。

## 验收发现与边界

1. 包源码身份检查拒绝符号链接，首版环境装配由链接改为复制普通文件；原日历校验没有放宽。
2. 部分数据测试仍导入旧 api.app，连带加载产品 SQLAlchemy。改接正式数据入口；同文件中的原生流水线断言单独保留，数据断言未减少。
3. 新闻注册使用 functools.partial，静态库存导出原先假定所有工厂都有 __name__ 而失败。现记录底层函数身份，既不调用工厂，也不序列化绑定参数；增加私密参数不出现在库存中的断言。Provider 集合更新为实际 27 个，新闻默认关闭仍有断言。
4. Tushare 测试的残缺 SimpleNamespace 配置改为真实 DataServiceConfig，保留凭据轮换等断言。隔离 HOME 下 Node 通过原已安装 VOLTA_HOME 使用工具链，不读取业务凭据。
5. NAV 单元夹具使用虚构日历发布信息，主仓严格拒绝。跨仓场景改用真实已核验日历读取，生产验证器未改。

DG1 是本地受控数据运行门禁。DG2a 原生绑定、DG2b 原存储保全/正式目标升级、DG2c 恢复及 DG3 真实新闻消费继续保留未完成。没有启用来源、模型实验、通知或交易；D9a/D10 和数据保全仍须先于相应产品源码删除。
