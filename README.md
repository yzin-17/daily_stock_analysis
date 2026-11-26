# DSA 数据服务（ThesisLedger Fork）

本仓为 ThesisLedger 获取、标准化、校验、缓存和提供市场事实。`/api/v1/thesis-ledger` 下的 `dsa-data-v1` 和 `dsa-control-v1` 覆盖行情、K 线、交易日历、事件、NAV、公共规则、公告、目录、OAuth 与新闻数据。能力范围以来源目录、策略和准入为准。ThesisLedger 负责研究、模型推理、观点与账户业务。

当前实施进度与验收边界由[纯数据服务 Task](docs/archive/tasks/2026-10-18-data-only-service.md)维护，合同见[规格](docs/specs/2026-10-18-data-only-service.md)。本地测试、独立进程、真实来源、定制 SDK/镜像及目标升级恢复是不同证据层。

## 本地运行

在项目 Python 环境中安装 `requirements.txt`，按 `.env.example` 配置数据服务和认证。启动：

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

`/health` 提供健康检查，`/openapi.json` 展示当前数据路由。认证数据/控制接口使用各自 Token；来源启用、凭据引用、路由和准入分别管理。服务入口为 `server.py` / `api/data_app.py`。

## 数据与维护入口

- `data_provider/`：原有数据适配器及来源工厂。
- `src/data/`：独立数据存储、指标和股票索引资产。
- `scripts/refresh_stock_index.py`：索引维护；种子位于 `src/data/assets/stocks.index.json`。
- `scripts/admit_nav_research.py`：按明确证据执行准入；必要 SDK、公告和 OAuth 维护脚本继续保留。
- `scripts/data-runtime-contract.json`：`dsa-data-runtime-v1` 镜像/代码载荷与持久化合同。

控制/OAuth 状态、凭据引用、公告原件、获取状态、目录任务以及既有数据库、报告和缓存按规格保全。源码退役不删除用户数据。

## 验证与部署

```bash
bash scripts/ci_gate.sh syntax
bash scripts/ci_gate.sh deterministic
bash scripts/ci_gate.sh offline-tests
```

默认 pytest 与 CI 共同消费 `tests/data-test-scope.json`；新增路径和混合断言必须明确登记。数据维护快速检查使用 `bash scripts/test.sh all`。

本机目标更新遵循[同级工作区规则](../AGENTS.md)和[infra 更新流程](../thesis-ledger-infra/docs/guides/docker-update-workflow.md)，根据兼容预检选择 `sync-code.sh dsa` 或 `update.sh dsa`。镜像独立验收使用仓库维护的 `scripts/verify_data_image.sh`；目标升级、恢复和实源门禁由 Task 分别记录。

## 开发与历史

完整文档见[文档中心](docs/README.md)，开发规则见 [AGENTS.md](AGENTS.md)，变更见 [CHANGELOG](docs/CHANGELOG.md)，许可证见 [LICENSE](LICENSE)。原生产品与客户端的历史资料保留原链接和 Git 历史，当前运行入口以本页及数据服务 Task 为准。
