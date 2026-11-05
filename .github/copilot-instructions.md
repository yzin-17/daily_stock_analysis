# 仓库协作指令

唯一真源为 [AGENTS.md](../AGENTS.md)，冲突时遵循该文件。

本仓是 ThesisLedger 的独立数据服务。数据实现维护在 `src/`、`data_provider/`、`api/`，部署与工程入口维护在 `scripts/`、`.github/workflows/`、`docker/`。服务通过 `server:app` 装配，镜像与快更遵循 `dsa-data-runtime-v1` 合同。

- 保留未提交改动；提交、tag、push 和对外消息需要明确授权。
- PR/Issue 工作遵循 AGENTS 的基线同步、必要性、范围、证据和恢复要求；脏工作区不得强行 pull、stash、reset 或覆盖。
- 不写入密钥或环境专属值；优先复用现有模块、配置、脚本和测试。
- 来源、路由、凭据和准入分别管理；未知状态、配置失败与缺失必须区分。
- 修改 API、配置、存储或部署行为，同步专题文档和 CHANGELOG；README 只保留项目定位、总览、快速开始与入口。
- CHANGELOG 的 `[Unreleased]` 采用扁平的 `- [类型] 描述`，不新增分类标题。
- 数据/消费者、物理环境、SDK/镜像、目标升级恢复和实源验证分别记录，不以局部通过替代外部门禁。

## 验证与治理

先定向测试，再按成本执行数据边界、lint、完整数据测试和目标运行验证。默认 pytest 与 CI 使用明确的数据测试清单；未登记测试不能静默忽略。

治理资产变更执行 `python scripts/check_ai_assets.py`。`CLAUDE.md` 保持为指向 `AGENTS.md` 的软链接，分层指令在 `.github/instructions/`，工程协作 skills 在 `.claude/skills/`。执行 Docker 操作前必须读取同级工作区的正式入口约束。
