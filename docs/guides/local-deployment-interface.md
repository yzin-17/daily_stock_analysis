# 本地部署维护接口

DSA 维护 SDK、快更载荷和运行语义，infra 维护实际部署编排。入口为 `bash scripts/local-deployment.sh <操作> <参数>`。

| 操作 | 参数与输出 |
| --- | --- |
| build-config | 绝对构建目录；标准输出为声明应用构建上下文的 Compose JSON |
| prepare-build | 绝对构建目录、绝对缓存目录；预取并校验固定版本 SDK 源码 |
| cleanup-build | 本轮绝对构建目录；只清理应用创建的临时源码和输出目录 |
| sync-preflight | 容器 ID、工作目录；只读核对合同、依赖与原生输入、命令和代码挂载 |
| sync-payload | 新的绝对载荷目录；装配允许快更的源码与资产 |
| snapshot-state | 无参数；将已运行 DSA 的 SQLite 在线备份写到标准输出 |
| archive-state | 无参数；将应用状态文件归档写到标准输出，排除已单独备份的 SQLite 文件 |

SDK 源预取使用 Node.js 24 和 Git，无需宿主 Python。`scripts/sdk-source.json` 统一声明固定 revision、tag 和产物版本，供宿主预取与镜像内 Python 构建器共同消费；`LONG_BRIDGE_SOURCE_CACHE` 可覆盖缓存目录。缓存复用仍检查固定 revision、Git 对象与工作树。infra 在保卷重装停机前调用 prepare-build；复用已验镜像时跳过预取。

`scripts/deployment/runtime-contract.mjs` 接受操作名及标准输入 JSON 参数数组，提供 snapshot、validate、override、config 和 restored；原快照格式和运行保护保持。此命令不操作 Docker 或业务数据；infra 负责快照持久化和容器恢复执行。

`scripts/deployment/restore-sqlite.py <源副本> <隔离目标目录>` 供主仓研究恢复演练消费，维护应用数据库文件名并验证 SQLite 完整性；拒绝覆盖已有目标数据库，源副本保持只读。该程序由业务验收环境的 Python 执行，infra 不持有或执行恢复程序。

字节码维护由 `bash scripts/deployment/prune-bytecode.sh [--apply]` 提供，默认预览；通过 infra 的 `locked` 命令使用正式维护锁与空闲 builder 检查，只清理源码目录可再生成的缓存。持锁命令须在应用 `scripts/deployment/maintenance.json` 登记，由 Node 包装验证祖先锁及固定 Docker context；容器内的 Python 仍归 DSA 维护。SQLite 备份同样只在已校验标签的 DSA 容器内运行应用 Python，不引入宿主依赖。

确定性验证：`node --test scripts/deployment/*.test.mjs scripts/prepare-local-sdk-source.test.mjs`；在 DSA 的 Python 环境执行 `python -B scripts/deployment/prune-bytecode.test.py` 和 `python -B scripts/deployment/snapshot-state.test.py`。真实更新仍使用[infra 入口](../../../thesis-ledger-infra/docs/guides/docker-update-workflow.md)，不能独立运行容器内替换脚本绕过正式门禁。
