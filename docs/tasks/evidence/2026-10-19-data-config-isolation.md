# 数据配置隔离证据

对应 [Task D1a/D1b](../../archive/tasks/2026-10-18-data-only-service.md) 与 [D0 盘点](2026-10-19-data-service-dependency-inventory.md)。本文件记录受控配置与临时存储验证；目标 Docker、真实来源和数据恢复分别由后续门禁验收。

## D1a：独立加载器

`src/data_config.py` 只拥有 D0 确认的 42 个数据、搜索、缓存、路径和 SQLite 字段，提供显式映射解析、`ENV_FILE`/缺省 `.env` 加载、进程缓存及重置。加载过程不实例化 SDK、任务或数据库。原 `Config` 保留字段顺序和直接构造合同，环境加载委托相同解析器；原生产品字段留在原处。

修改前捕获 `tests/fixtures/data_config_legacy_v1.json`：旧 `src/config.py` 输入 SHA-256 为 `1584637959c26d53c51df0b13be1d710c56c356dbeb47f7310ae1b042c941390`。7 组输入覆盖缺省、全部显式值、空值差异、无效值/上下界、布尔差异、旧非有限数语义，以及环境变量覆盖文件；全部使用合成凭据且网络尝试为零。冻结样例未从新实现重新生成。旧解析器委托后再次逐字段核对，7 组均一致。

保留既有公共 SearXNG 布尔缺省、Tushare 优先级、代理别名及国内域名绕过语义；配置解析本身不构成来源启用或请求。凭据和私有搜索地址不进入配置 `repr`，无效值日志只含字段/回退值或数量。可选文件缺失继续表示缺省配置；已有路径不是文件或读取失败抛出 `DataConfigurationError`，失败不缓存为空凭据。

| 检查 | 命令/范围 | 结果 |
| --- | --- | --- |
| 冻结基线与隔离进程 | `python -m pytest tests/test_data_config.py -q`；7 组直接解析、7 组真实 dotenv/缓存子进程及秘密/失败边界 | 20 项通过；子进程阻止产品/模型 SDK 导入和 socket 连接 |
| 既有解析兼容 | 上项加 `tests/test_config_env_compat.py tests/test_news_strategy_config.py tests/test_config_validate_structured.py` | 142 项通过；2 条既有测试依赖弃用提示 |
| 新代码 lint | `python -m flake8 src/data_config.py tests/test_data_config.py --count --statistics` | 0 |
| 关键错误检查 | `python -m flake8 src/config.py src/data_config.py tests/test_data_config.py --select=E9,F63,F7,F82 --count --statistics` | 0 |

以上命令使用仓库 `.venv/bin/python`。

## D1b：数据配置调用方

数据 Provider、FetcherManager 及环境凭据桥接已改读独立加载器；查询选择、凭据原值、环境/页面配置优先级、快照修订与 OAuth 引用格式保持。原搜索模块按 D2c 继续；原生应用装配由 D3a 处理。索引和日志模块没有旧 Config 导入，此叶未改写其无关行为。

`read_config_fields` 的正常空值仍返回空字典；真实加载失败保留 `DataConfigurationError`，其他内部异常转换为不带秘密的配置错误。未知来源不触发加载。Longbridge 环境桥接、可用性检查和 TickFlow TTL 不再吞掉配置失败；Longbridge 的 SDK 语言参数由 Adapter 自己解析，保留原英文/中文别名及韩文时不传该 SDK 参数的行为，不再导入产品报告或决策 Schema。

新增 `tests/test_data_configuration_consumers.py` 的三个全新解释器进程，使用真实 dotenv、来源注册、环境读取和凭据编解码，覆盖真缺失、Legacy 和 OAuth；阻断旧 Config、报告/决策、模型 SDK 导入及 socket 连接。5 项实际配置路径失败检查确认失败没有变成“未配置”。SDK 参数测试只替换外部 SDK 元数据，解析运行真实 Adapter 方法，禁止建立连接。

| 检查 | 输入范围 | 结果 |
| --- | --- | --- |
| 配置调用方、持久化/修订与行情回归 | 下方 25 文件；生产修改稳定后执行，新增 5 项失败边界另跑所属文件 | 426 项通过，加新增 5 项通过；合计 431 个不同用例，7 条既有弃用/收集提示 |
| 新增调用方隔离与失败边界 | `python -m pytest -q tests/test_data_configuration_consumers.py` | 17 项通过 |
| 新增代码 lint | `python -m flake8 src/data_config.py src/services/thesis_ledger_environment_credentials.py tests/test_data_configuration_consumers.py --count --statistics` | 0 |
| 全仓关键错误检查 | `python -m flake8 . --count --select=E9,F63,F7,F82 --statistics` | 0 |
| 差异空白 | `git diff --check` | 0 |

可复现的定向输入：

```sh
.venv/bin/python -m pytest -q \
  tests/test_capital_flow_request_scope_context.py tests/test_yfinance_financial_period.py \
  tests/test_finnhub_fetcher.py tests/test_fetcher_source_optimization.py \
  tests/test_thesis_ledger_environment_credential_lifecycle.py tests/test_provider_credentials.py \
  tests/test_thesis_ledger_tickflow_capability_path.py tests/test_hk_realtime_routing.py \
  tests/test_tickflow_manager_routing.py tests/test_realtime_quote_fallback_logging.py \
  tests/test_chip_distribution_manager.py tests/test_thesis_ledger_tushare_mapped_read.py \
  tests/test_thesis_ledger_control.py tests/test_longbridge_fetcher.py \
  tests/test_thesis_ledger_tushare_market_v3.py tests/test_fundamental_valuation.py \
  tests/test_fundamental_context.py tests/test_exact_provider_configuration.py \
  tests/test_alphavantage_fetcher.py tests/test_thesis_ledger_tushare_events_v3.py \
  tests/test_thesis_ledger_tushare_event_registry_v3.py tests/test_thesis_ledger_provider_credentials_runtime.py \
  tests/test_thesis_ledger_tushare_credential_revision.py tests/test_data_configuration_consumers.py \
  tests/test_data_config.py
```

来源注册/准入、运行环境及实际配置文件没有改动。这里的临时 SQLite 与受控解析不替代 D2b/DG1/DG2 的独立存储、进程、留存和恢复验收。

## D2c：搜索取数与数据诊断

搜索服务改从独立配置读取新闻窗口、搜索凭据与地址；现有查询、原文提取、缓存和超时实现保持。七种现有 Adapter 为 Bocha、Tavily、Brave、SerpAPI、MiniMax、SearXNG、Anspire；本叶不默认新增启用或实际访问任何一家。凭据轮换错误日志不再包含 key 前缀。数据诊断模块本身没有产品/模型导入，保留原数据诊断合同。

新增 `tests/test_data_search_isolation.py`，两个新解释器进程阻断旧 Config、整体存储、Agent/LLM/通知/报告与 socket；运行真实配置和搜索服务。缺配置场景零 SDK 查询；受控 Tavily SDK 响应经过实际 Adapter、过滤、缓存与诊断，正向和超时均有记录，重复缓存不再查询，模型答案参数保持关闭，诊断和日志不含合成 key。原文库使用当前实际安装版本，没有替换整个搜索实现。

`.venv/bin/python -m pytest -q tests/test_data_search_isolation.py tests/test_search_service_concurrency.py tests/test_search_news_freshness.py tests/test_search_searxng.py tests/test_anspire_search.py tests/test_search_serpapi_provider.py tests/test_search_tavily_provider.py tests/test_run_diagnostics_p1.py`：166 项通过，2 项 Anspire 实源集成因未提供测试凭据跳过，2 条既有弃用提示。新增文件完整 flake8 为 0。有界进程超时、并发合并及新闻时间/过滤断言由该组既有测试覆盖。

N2 仍须实现独立新闻合同适配，N3 才注册正式数据路由；本叶不是新闻生产或真实来源验收。

## 原生入口退役前的配置重载兼容

接入独立生命周期时补核对发现：旧配置重置还须使新增的数据配置缓存失效，否则原生入口尚保留期间的显式环境重载会留下旧凭据。`Config.reset_instance()` 现同步调用数据缓存重置；数据模块仍不反向导入旧 Config。新增前后凭据重载断言，加原配置/配置管理器回归 `.venv/bin/python -m pytest -q tests/test_data_config.py tests/test_config_env_compat.py tests/test_news_strategy_config.py tests/test_config_validate_structured.py tests/test_config_manager.py`，152 项通过。固定旧解析样例未改变。
