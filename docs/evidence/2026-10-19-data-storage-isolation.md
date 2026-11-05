# 数据存储隔离证据

对应 [Task D2a/D2b](../archive/tasks/2026-10-18-data-only-service.md) 与 [D0 存储清单](2026-10-19-data-service-dependency-inventory.md)。所有数据库均为临时合成副本，没有读取或改写活动业务数据库。

## D2a：FX 原表兼容

`FxRateRepository` 独立拥有既有 `portfolio_fx_rates` 数据缓存；新库只初始化这一张表及相同唯一键/索引。正式 FX HTTP 改为调用独立数据装配，逐请求关闭连接，不再导入原生组合服务/仓储或整体 `DatabaseManager`。原生组合取数方法暂时委托共用 Yahoo Adapter，其他组合业务与旧模型保持。

修改前从原模型声明捕获 `tests/fixtures/fx_cache_legacy_v1.sql`，捕获过程没有访问数据库。兼容测试用该 SQL 建立旧表，保留合成其他业务表与未来日期记录，验证原位读取、同日 upsert 不换主键、列类型/空值/主键/唯一键/索引一致，关闭后可重开，其他表与记录不变。没有统一迁移控制/OAuth 或旧业务数据库。

保留 7 天缓存上界、直接优先/反向求倒数、身份汇率、过期失败为不可用、缺失不补数，以及未知时区不输出 `fetchedAt`。新查询仍采用原 Yahoo 7 天窗口和最后有效日线收盘值，以请求 `asOf` 登记缓存日期；本叶不改变这一既有时间合同或宣称新增历史覆盖。SQLite 继续使用配置中的 WAL、busy timeout 与有界写重试，保留既有无时区缓存时点语义。

| 检查 | 命令/范围 | 结果 |
| --- | --- | --- |
| 既有缓存时点/合同 | `.venv/bin/python -m pytest -q tests/test_thesis_ledger_fx_cache_timestamp.py tests/test_thesis_ledger_contract.py`；同批检查的这些文件输入随后未变化 | 16 项通过并复用 |
| 旧表、新库、缓存/来源与原生兼容 | `.venv/bin/python -m pytest -q tests/test_data_fx_rates.py tests/test_portfolio_pr2.py tests/test_portfolio_service.py` | 70 项通过，含 13 项新 FX 验证；5 条既有依赖/收集提示 |
| 正式 HTTP 独立导入 | 新解释器运行实际 Router/认证/数据仓储，读取临时旧表；阻断旧 Config、整体存储、组合服务、Agent/模型/通知导入与 socket | 401 拒绝及 200 正向均通过，未知时区不输出抓取时点 |
| 新 FX 与指标代码 lint | `.venv/bin/python -m flake8 src/data/fx_rate_repository.py data_provider/fx_rates.py src/services/fx_rate_service.py tests/test_data_fx_rates.py tests/test_thesis_ledger_fx_cache_timestamp.py src/data/technical_indicators.py src/services/technical_indicator_series.py tests/test_data_indicator_kernels.py --count --statistics` | 0 |

合计 86 个不同用例通过。这里的 HTTP 为真实模块处理受控旧数据；DG1 仍须覆盖完整数据路由矩阵，目标 Docker/真实来源/留存恢复由后续独立门禁负责。

## D2b：控制/OAuth 与公告

已核对控制存储 10 张表、同一 SQLite 中的 OAuth 会话表，以及独立公告目录/`.acquisition` 文件状态。它们不依赖整体 `DatabaseManager` 或决策模型，保持既有位置、加密格式与租约机制，没有统一迁库。

核对时复现控制存储连接只退出事务、未显式关闭的问题：保留连接引用的测试在修改前失败。连接/事务职责现由 `thesis_ledger_control_sqlite.py` 明确拥有；所有既有调用均使用上下文，保持 WAL/busy timeout/外键与事务提交/回滚行为，成功、失败和初始化异常均释放连接。原控制大文件缩小，未扩大尺寸阈值。

`tests/test_data_storage_isolation.py` 的全新解释器阻断产品、模型 SDK、整体存储/决策导入及 socket，执行真实加密写入和重开：配置/凭据修订保持、OAuth 成功状态/加密 token 引用保持、两个来源仍关闭、11 张所需表之外没有原生业务表；公告 JSON 与原件字节保持，过期租约可由新 owner 领取，旧 owner 的晚到提交被拒绝，第二次提交收敛到终态。另有连接成功/回滚后已关闭的直接断言。

`.venv/bin/python -m pytest -q tests/test_data_storage_isolation.py tests/test_provider_oauth_store.py tests/test_provider_oauth_runtime.py tests/test_provider_oauth_native_storage.py tests/test_provider_oauth_manager.py tests/test_provider_oauth_api.py tests/test_provider_credentials.py tests/test_thesis_ledger_control.py tests/test_thesis_ledger_control_v3.py tests/test_thesis_ledger_announcement_jobs.py tests/test_thesis_ledger_announcements.py tests/test_thesis_ledger_announcement_routes.py`：87 项通过，5 条既有提示。定制 Longbridge 原生绑定的 1 项离线接线测试因当前环境未安装定制 wheel 跳过；其真实绑定验证仍交目标门禁，不计为本地通过。

D3b 仍须装配公告/OAuth/目录等运行任务的启动与停止；本叶验证存储，不代替应用生命周期、目标卷留存或恢复。
