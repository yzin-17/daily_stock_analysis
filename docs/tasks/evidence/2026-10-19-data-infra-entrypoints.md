# D10 数据部署入口与运行态恢复证据

日期：2026-10-19。对应 [Task](../../archive/tasks/2026-10-18-data-only-service.md) D10a/D10b/D10r/D10c；本轮只做源码及本地定向验证，未切换目标运行态。

## 源码组合与启动合同

验证组合为当前未提交工作区：DSA 基于 `dd26571509e842ab230c393f5f339911ea98425d`，infra 基于 `3f661df1deb3de894755dcc66e584abb4165d014`。这些 HEAD 本身不包含本次修改，不能单独作为可部署版本。当前组合要求 `dsa-data-runtime-v1`，Data/Control Contract 均维持 V3；最终发布仍须不可变镜像和独立运行验收。

关键输入的 SHA256：

| 仓库与路径 | SHA256 |
| --- | --- |
| DSA `docker/Dockerfile` | `41f70c46f80c123f6ec881b32685ad20a84ba192073b5419474ffbb58f5a6bda` |
| DSA `scripts/data-runtime-contract.json` | `e3e58d990a964b10a5cfa3989d2c5d8321764f2aad563d740b6c99a9938686b6` |
| DSA `scripts/data_runtime_compatibility.py` | `f566dc3f16212dc5171c7d72020aa166702472d83d222c7ac07a5852d1631f69` |
| infra `compose.yml` | `99e391c1493e2b0a402095f2cce04073191c8986c13d474246f5f7d81b4d7b31` |
| infra `compose.dev.yml` | `1e195168bfad7ea6eadbdc631e931354226e1d6aa60ae3e74379718dae2848ab` |
| infra `scripts/update.sh` | `b7a17631cdfbf50083242d33e6b23a6dfee2148c4f43fed36bceb0d49195ef1a` |
| infra `scripts/update-runtime.sh` | `81db95a84861d51c567a39a4e9764484b7bd82b7ff7f6eb26f79a53bb83d6b28` |
| infra `scripts/dsa-code-payload.mjs` | `6846a8d2129950898afcc0bdf278d96266702845f1226431221bbc448a8138d0` |

Compose 使用 `python -m uvicorn server:app --host 0.0.0.0 --port 8000`。本地 Dockerfile 变换不再要求 Web/npm 阶段，保留 Rust 1.94.1、Python 3.11、原生 SDK 编译/存储接口验证、APT/pip 缓存及预取源码约定。普通更新与保卷恢复使用同一基础镜像集合。

## 快更与兼容拒绝

镜像构建保存 `data-runtime-inputs.json`，指纹包含依赖、Dockerfile/entrypoint、SDK 补丁/构建脚本和 SQLite 结构所有者文件。infra 宿主 Node 与 DSA Python 对实际源码给出一致文件摘要。快更在复制前要求容器健康、镜像引用一致、数据合同/指纹匹配、Uvicorn 命令一致且代码路径未挂载；旧镜像须完整更新。

载荷仅包含合同指定的数据入口、代码目录和脚本；数据、报告、日志、OAuth 存储不进入载荷。容器内清单必须完整且无重复，替换前保留旧代码，中途失败回滚全部已改路径。复制或健康失败只从原镜像重建 DSA，禁止拉取。成功后再次核对镜像 ID；快更不构成镜像更新证据。

## 正式恢复

`update.sh` 在持锁后、空间整备/构建前记录原不可变镜像、API 命令、entrypoint、健康、端口和全部实际挂载，并保护原镜像。快照只保存环境摘要，不保存环境值。命名卷、匿名日志/报告卷及绑定路径通过持续挂载 override 接入后续更新与快更；卷使用原精确名称且禁止自动复制镜像内容。

恢复命令为 infra 的 `ENV_FILE=.env bash scripts/restore-dsa.sh <更新前快照.json>`。原 context、环境文件摘要、镜像及所有挂载必须存在；合并后的端口/挂载必须完全一致，恢复后重新核对合同与健康。入口只重建 DSA，不构建/拉取、不操作数据库或删除卷，失败保留现场。支持原 `serve-only` API 与数据 API；定时分析命令拒绝恢复。快照恢复镜像代码基线，容器可写层快更和历史数据时点另属状态恢复边界。

## 验证结果与边界

- infra：5 个 Node 套件共 **40 项通过**，覆盖合同/跨语言指纹、正式快更入口、实际文件事务、恢复语义及正式恢复入口。入口测试使用假 Docker CLI；文件事务只把固定 `/app` 根映射到自有临时目录，实际执行替换/回滚流程。
- infra：`update-dsa-dockerfile.test.sh`、`sync-code.test.sh`、`reuse-dsa-image.test.sh`、`rollback-image-update.test.sh`、`build-space.test.sh`、`reinstall-preserving-volumes.test.sh` 全部通过；兼容清单和 9 个受影响 Shell 文件语法检查通过。既有超长 `update.sh` 的阶段编排提取为职责明确模块，入口和 `build-space.sh` 均缩小。
- DSA：运行合同、构建指纹、镜像验证脚本的 **25 项定向测试通过**；`ci_gate.sh syntax/flake8/deterministic` 及新增/修改 Python 模块完整 lint 通过。执行时 PATH 显式优先项目 `.venv/bin`；宿主全局 `python` 为不兼容架构，未据此修改项目运行时。
- D9a 的完整物理数据环境 **6,154 项通过**证据继续复用；本次新增兼容性断言由上述定向测试覆盖，不把历史全量数字扩算为本次结果。
- 三仓 `git diff --check` 通过；未暂存、提交或发布，未运行真实 Docker 状态变更、镜像构建、目标更新或 Provider/模型探针。

D10 的脚本实现与定向验收完成。DG2a 的实际 SDK/镜像、DG2b 的目标保留升级、DG2c 的真实恢复和 DG3 的实源验收继续保持未完成；源码退役按 D4～D8 的精确闭包继续。
