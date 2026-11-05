# 数据打包、测试范围与闭包门禁证据

对应原 Task 的 D9a1～D9a3。D10 接续消费本合同。本轮没有构建实际镜像、执行目标同步或改变容器。

## 运行载荷

`scripts/data-runtime-contract.json` 固定 `dsa-data-runtime-v1`：默认命令为 `python -m uvicorn server:app --host 0.0.0.0 --port 8000`，载荷包括 server.py、api、data_provider、src、保留的 NAV 准入脚本及合同本身，索引位于 src/data/assets。Dockerfile 与独立 Compose 使用数据入口，构建不需要 Web 阶段、Bot 或策略目录；Provider 目录中的 JP/KR 等数据资产保留。

Longbridge Rust/wheel 构建、加密存储接口版本检查、全局 Futu、RQData 基金扩展检查保留。数据、日志、历史报告目录及 Longbridge OAuth 存储路径保持；健康失败不返回成功。`check_data_runtime_contract.py` 的 11 项正反向测试核对载荷、命令、标签、资产、健康和存储。

D9a 保留原产品源码与现有依赖，D4～D8、D9b 负责后续物理移除；当前复制范围不表示最终镜像已完成收敛。

## 测试路径与断言

`tests/data-test-scope.json` 绑定 D0 库存原字节摘要。首次审查登记 576 份测试：373 份数据文件、2 份按函数分配的混合文件、201 份原生产品测试。D4a2 复核已恢复其中两份数据工程/策略备份恢复测试，并修正默认收集和分片入口；当前清单及验证见[退役证据](2026-10-19-data-product-retirement.md#d4a2api-装配与明确测试收集)，不能把此处首次审查数字当作最终覆盖。产品测试源码的实际删除仍满足对应闭包和数据保全前置。

`scripts/data_test_scope.py` 拒绝新增未登记文件、数据文件丢失、混合文件出现未分配断言、D0 数据标记静默改归产品、基线字节改变。正常输出 376 个数据测试目标，CI 分片和本地物理环境共用这份清单。

| 原混合职责 | 数据断言与产品断言的出口 |
| --- | --- |
| 名称预取、报价日志、诊断及新浪指数 | Provider 断言留在数据文件；流水线、告警、队列和大盘消费者另存原生文件。数据事件保留来源节点和实测耗时断言 |
| 配置及指标 | 固定旧字节基线、全部纯函数输出继续执行；仅 Config 重载与原生包装器委托比较单列 |
| JP/KR/TW | 标的、报价、日历、路由断言保留；原生提示词和账户写入集成断言单列 |
| 旧 SPA 索引部分 | 缓存新旧/缺失/损坏、种子热路径、404、刷新回调迁入正式数据入口测试；启动关闭由数据生命周期拥有 |
| API 错误正文 | api/errors.py 拥有通用 HTTP 契约，数据入口实际消费，旧 V1 只重导出；原断言保留，数据测试不再初始化 V1 产品存储 |
| 全局 SDK 与桌面包 | SDK/数据镜像验证继续执行；桌面冻结包及原生定时分析工作流按函数登记产品归属 |

D0 将 stock_daily_window_resolver 标作数据保留；当前引用只有原生 backtest_service 和 skill_opinion_outcome_service。清单显式记录其原生分析后效窗口所有权，单元随 D8c2/D8b2 退役；Data V3 行情窗口断言继续保留。其余带数据依赖的产品测试也在清单逐路径记录职责复核，未用通配 ignore 隐藏失败。

补验修正了旧夹具：Provider 注册使用完整 DataServiceConfig；TW 机构资料预算测试替换实际 data_config 入口，超时和禁用断言不变；Anspire 收集不自动加载项目 .env，改用受控数据配置。

## 闭包及 CI

`src/data/runtime_inventory.py` 由环境装配与门禁共用，解析父包初始化、相对导入、函数内延迟导入、字面量动态导入。门禁从 server.py 和全部 Provider 出发，核对 368 份源码。`check_data_service_boundary.py` 禁止产品、账户/分析服务及模型 SDK 回流；10 项正反向测试覆盖直接/延迟/包导出/动态导入和正常数据编译，编译不写入 pyc。

两个已复核动态点为服务包的明确延迟映射和正式 Registry 的 Adapter 加载。新点或表达式变化要求复核；数据调用方从包映射导入 StockService 等原生类时，会展开真实目标并拒绝。

扩大静态闭包发现原虚拟环境实际缺少 rqdatac 模块。离线装配在 baselineMissingModules 保留缺失，不安装或伪造；数据闭包仍为 97 包，含 pytest 为 101 包，12 个已有数据 SDK 可见性不变。生产 requirements 和镜像中 RQData 基金扩展安装/接口检查保留，实际目标由 DG2a 验证；这补充 DG1 的缺失 SDK 清单。

CI 保留三分片、失败汇总与镜像缓存；按数据源码、索引、原生 SDK、测试清单和门禁输入触发。语法只编译数据闭包，确定性阶段检查载荷/测试范围，离线分片只收集明确数据断言。Web/桌面构建随功能退役，数据 SDK 和 Longbridge 原生编译保留。发布预检接同一数据入口，保留版本元数据，移除 Web 构建参数；没有触发发布。

`scripts/verify_data_image.sh <本地镜像>` 验证已有镜像，`--build` 是 CI 显式构建模式并先查源码合同。脚本只创建唯一自有标签容器，禁用外部网络、只读根文件系统，数据/日志/报告/OAuth 使用临时挂载，不发布端口或挂载业务数据。不隐式 pull、不删除镜像或卷；失败只回收自有标签，冲突不操作已有资源。检查 HTTP 路由、索引、SDK 存储接口及正常退出。假 CLI 已验证构建、启动、健康、SDK 预检和退出失败路径；尚未执行真实 Docker。

非敏感参数为镜像、DSA_SMOKE_POLL_ATTEMPTS（默认 30）、DSA_IMAGE_BUILD_PYTHON（构建合同检查解释器）和公开版本/提交元数据。该脚本不替代目标 update.sh/sync-code.sh，D10 须先完成兼容、载荷和恢复出口。

## 结果与边界

- 完整明确数据范围：**6,154 passed、1 skipped、4 deselected、0 failed/error**，189.53 秒；376 目标涉及 375 文件，部分按函数收集。完整日志、XML、参数位于 `/private/var/folders/t6/qhk7wqzd07g25xwy7mr46gb00000gn/T/dsa-data-runtime-tqly9yob`。
- skip 是定制 Longbridge SDK wheel 原生存储验证；4 项 network 未执行，真实来源资格未变。
- 共享导入分析器及缺失 SDK 记录完成后，重新离线装配并冷启动：7 项独立启动/HTTP 与 8 项假 Docker CLI 检查共 **15 passed**。此结果与完整矩阵重叠，不累计。
- 数据语法/闭包、关键 flake8、确定性门禁、新 Python 门禁模块完整 flake8 通过。DG1 主仓客户端真实 TCP 消费 6 项通过；错误正文重归属不改变 wire 语义。

实际镜像、定制原生 SDK、目标保全/升级/恢复及真实新闻消费继续由 DG2a/DG2b/DG2c/DG3 验收。本轮没有改业务数据库、暂停开关、来源准入、真实模型或通知，也没有提交或发布。
