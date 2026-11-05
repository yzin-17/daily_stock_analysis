# 专用数据应用与生命周期证据

对应 [Task D3a/D3b](../archive/tasks/2026-10-18-data-only-service.md)。

## D3a：专用 ASGI 装配

`server.py` 改用独立数据配置/日志与 `api.data_app.create_app`，装配六组现有 V3 数据/控制/OAuth Router、两处健康接口及数据索引。沿用 Data/Control Token 分离、CORS 配置和结构化 HTTP 错误；请求上下文保持关联 ID。旧 V1/V2 前缀明确返回既有 `404 {error:not_found}`，没有 SPA 回退或产品接口装配。内部异常只记录类型，配置内部失败返回 503，与真缺凭据分开，不向日志或响应输出异常中的私有值。

`.venv/bin/python -m pytest -q tests/test_data_app.py`：25 项通过，2 条既有依赖弃用提示。覆盖健康、数据/控制认证、V3 错误关联、旧产品路径多方法退役、通用/配置错误秘密保护；新解释器从实际 `server:app` 启动并运行真实 ASGI HTTP/索引读取，阻断旧应用、V1、整体存储、Config、原生 scheduler、Agent/LLM/通知/报告及模型 SDK 导入和 socket。没有运行模型、来源或目标容器。

`.venv/bin/python -m flake8 api/data_app.py server.py tests/test_data_app.py src/services/thesis_ledger_control_sqlite.py tests/test_data_storage_isolation.py --count --statistics` 为 0；全仓关键错误 lint 为 0。

D3a 的独立装配通过不等于数据后台工作就绪。公告、OAuth、目录和索引的完整生命周期仍由 D3b 独立接续；当前没有切换目标运行态。

## D3b1：目录工作单元

目录调度已从控制大文件提取到专属 worker，复用原 Provider 隔离进程。进程等待、重试退避及发布前检查响应同一停止信号；停止信号不进入 Provider 降级或失败提交。退出会终止并回收实际隔离进程，释放并发槽，等待调度线程结束；有在途且不响应的执行时明确抛出退出超期，不能返回清洁退出。

被打断任务仅在 owner、running 状态和有效租约匹配时改回 pending，下次启动复用原任务恢复，未生成目录不发布 generation。停止后的内存投递拒绝，已入库任务保留；初始化恢复先于线程启动，避免初始化失败留下孤儿线程。正常成功和原有 lease 失效恢复逻辑保持。

`.venv/bin/python -m pytest -q tests/test_data_catalog_lifecycle.py tests/test_thesis_ledger_catalog_job.py tests/test_thesis_ledger_catalog_sources.py tests/test_thesis_ledger_catalog.py tests/test_thesis_ledger_catalog_contract.py`：59 项通过，3 条既有提示。新增 6 项覆盖重复/空闲停止、恢复失败不留线程、真实隔离进程被回收且容量恢复、真实 SQLite pending 重开仅一任务/一 generation、错误 owner 拒绝释放，以及超期不报成功。所有响应与 Provider 操作均受控，没有上游请求。

`.venv/bin/python -m flake8 src/services/thesis_ledger_catalog_execution.py src/services/thesis_ledger_catalog_worker.py tests/test_data_catalog_lifecycle.py --count --statistics`：0。公告、索引及应用组合生命周期继续 D3b2～D3b4。

## D3b2：公告协作停止

公告运行时在分页、原件流读取、记录处理与终态提交前响应停止。专属停止信号越过普通来源失败处理，不写入伪造成功/失败；未完成任务保留原 running/owner/lease，后续按现有租约与重试上限恢复，晚到 owner 不能提交。重复启动不增加线程，正常/重复停止及部分线程启动失败均等待已有线程释放；退出超期明确失败。

获取函数显式关闭自己创建的 HTTP Session，调用方注入的 Session 仍由调用方拥有；原件响应流在停止时关闭。查询身份、分页、下载、解析、TTL 与来源资格保持。这里未新增公告来源或改路由。

`.venv/bin/python -m pytest -q tests/test_data_announcement_lifecycle.py tests/test_cninfo_announcements.py tests/test_thesis_ledger_announcement_jobs.py tests/test_thesis_ledger_announcement_routes.py tests/test_thesis_ledger_announcements.py`：25 项通过，2 条既有弃用提示。6 项新增验证覆盖重复启动/退出、部分启动失败回收、真实获取函数在受控流中取消并关闭 Session/响应、租约重领与晚到提交、调用方资源所有权、分页前取消无缓存写入及超期拒绝报成功。

这五个生产/测试文件的完整 flake8 为 0。受控流只替换外部传输，运行真实获取和持久任务逻辑；应用整体资源组合继续 D3b4。

## D3b3：索引刷新取消

索引下载改在可终止执行单元中运行，原缓存刷新函数仍在父进程执行，因此保留原刷新锁、失败计数/熔断、时效、原子写入和父进程名称缓存失效行为。默认远端 URL、缓存位置及开启状态保持。停止会发出取消信号，并等待实际下载进程、IPC 与工作线程结束；外层 task 取消也先完成同样回收，不只取消 await。

`tests/test_data_stock_index_runtime.py` 的 6 项验证使用受控本地 HTTP：真实 200 刷新使父进程已缓存的旧名称失效，重复请求合并、有效缓存不再取数，重复启动/停止保持；三次真实 503 后继续沿用旧缓存且父进程熔断，第 4 次没有请求；普通停止及外层取消均回收真实阻塞进程，执行启动失败不留进程或写缓存，关闭状态不创建执行任务。停止不计作来源失败。

该文件 6 项全部通过；同批已通过且输入未变的 `test_stock_index_remote_service.py`、`test_stock_index_loader.py`、`test_data_stock_index_asset.py` 共 37 项复用，合计 43 个不同用例。新过程/运行时/测试三个文件完整 flake8 为 0。没有访问公网或改活动缓存；数据应用组合继续 D3b4。

## D3b4：资源组合与 OAuth

专用 `data_lifespan` 成组启动索引、公告、OAuth 与目录执行器，并在正常退出、启动/应用异常时逆序释放；释放动作在启动前登记，一个资源退出失败也继续释放其余资源。重复进入活动生命周期明确拒绝，不破坏已有资源；结束后可再次完整启动。默认数据应用已接入该生命周期，没有装配原生分析 scheduler。

公告 start/stop 共享生命周期锁，启动期间取消会等待实际启动动作结束再关闭，避免仅取消外层 await 后仍有迟到创建。目录初始化由现有注册锁和先恢复后启动保护；索引退出等待实际下载单元与工作线程回收。OAuth 沿用原 manager/repository，关闭未完成授权时取消实际等待任务并保留 `OAUTH_RESTARTED` 终态，不打开真实授权页面。

`.venv/bin/python -m pytest -q tests/test_data_lifespan.py tests/test_data_app.py tests/test_data_announcement_lifecycle.py tests/test_provider_oauth_manager.py`：48 项通过，2 条既有弃用提示。12 项组合测试覆盖每个启动阶段失败、应用体失败、清理失败仍继续释放、活动生命周期重复进入、两次实际数据应用启动/退出、受控 SDK 等待任务关闭，以及公告启动尚未完成时取消。原独立解释器从 `server:app` 的无产品导入验证亦已在新生命周期下通过。受控外部传输/SDK 边界与真实业务实现分开；定制原生绑定及目标运行仍由对应门禁负责。

新应用/lifespan、关联测试、公告运行时与配置回归文件完整 flake8 为 0。D3b 各子叶完成，DG1 仍需独立服务进程的完整路由矩阵，N2/N3 继续新闻适配与注册。
