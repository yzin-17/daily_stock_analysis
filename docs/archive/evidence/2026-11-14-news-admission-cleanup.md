# 新闻准入工具残留清理验收

日期：2026-11-14。范围为本仓与同级 ThesisLedger 工作树；未提交。合同见[规格](../../specs/2026-11-14-news-admission-cleanup.md)，执行状态见[归档任务](../tasks/2026-11-14-news-admission-cleanup.md)。

## 原因与实际修改

2026-11-09 的删除覆盖了 DSA 数据源模块、注册、库存、当前政策和 Desktop 说明，但遗漏了 infra 中的宿主准入工具。2026-11-11 的迁移清单将四个旧名文件迁入本仓 `scripts/local-runtime/`，保留了旧默认配置及对已删除适配器的导入。旧入口同时承担其他新闻源的维护，并被两个同花顺入口消费，不能直接删除共享能力。

- 将原 `nvidia-news-admission.mjs`、`.py`、`.test.mjs`、`.test.py` 改名为同目录的 `news-admission.*`；移除旧来源的默认项、配置档、解析分支及失效导入。五个保留配置档必须显式选择，数据库辅助函数必须显式传入路由和目标。
- 修改 `hithink-arena-news-admission.mjs`、`hithink-news-pagination-admission.mjs` 的执行器导入；同步准入参数、证据与 SQLite 测试，增加缺少来源、未知来源和消费者导入验证。
- 更新本仓 `docs/guides/local-runtime-tools.md`、`docs/specs/2026-10-18-data-only-service.md`、`docs/CHANGELOG.md` 和文档导航。旧新闻证据仅修复迁移规格的链接。
- 将主仓 `docs/specs/2026-10-22-free-news-dg3.md` 移至 `docs/archive/specs/`，标明历史适用性并修复入链、出链；更新主仓现有来源、同花顺/Yahoo、快讯、Yahoo、续验和七日滚动等六份现行规格。
- 删除本仓 `data_provider/__pycache__/nvidia_news_rss.cpython-312.pyc`、`data_provider/thesis_ledger_sources/__pycache__/nvidia_news.cpython-312.pyc` 和 `tests/__pycache__/` 中的两个旧测试字节码；从 `.pytest_cache/v/cache/nodeids` 精确移除 29 条旧来源测试缓存，其余条目保留且 JSON 解析通过。

## 离线验证

以下命令从 `daily-stock-analysis` 执行，实际均通过 `rtk proxy` 调用。Python 维护测试设置 `PYTHONPATH` 为本仓绝对路径；使用 `-B` 或 `PYTHONDONTWRITEBYTECODE=1` 防止重新生成字节码。

| 命令 | 结果 |
| --- | --- |
| `node --test scripts/local-runtime/news-admission.test.mjs` | 14 项通过，包括五个配置档、必填选源、证据摘要、目标卷校验、扩展、续验、打包及两个消费者导入 |
| `.venv/bin/python -B scripts/local-runtime/news-admission.test.py` | 13 项通过，包括元数据、未知来源、缺库与事务内并发保护 |
| `.venv/bin/python -B scripts/local-runtime/news_admission_extension.test.py` | 6 项通过 |
| `.venv/bin/python -B scripts/local-runtime/news_admission_renewal.test.py` | 7 项通过 |
| `.venv/bin/python -B scripts/local-runtime/hithink_arena_news_admission.test.py` | 2 项通过 |
| `.venv/bin/python -B scripts/local-runtime/hithink_news_pagination_admission.test.py` | 3 项通过 |
| `.venv/bin/python -B -m pytest -q tests/test_thesis_ledger_provider_runtime.py tests/test_thesis_ledger_inventory_export.py tests/test_thesis_ledger_news_sources.py tests/test_thesis_ledger_capability_registry.py` | 964 项通过，3 条警告 |
| `.venv/bin/python -B scripts/check_thesis_ledger_capability_boundaries.py` | 通过 |
| `.venv/bin/python -B scripts/check_data_runtime_contract.py` | 通过；运行载荷不包含宿主 `scripts/local-runtime/`，本次无须部署服务 |

维护相关测试共 45 项，来源与目录测试 964 项。未调用真实来源、模型、Docker 或准入应用。

## 残留与文档检查

使用 `rg --hidden --no-ignore` 扫描两个应用的实际代码、测试、脚本及 infra 脚本，覆盖未跟踪文件和构建输出；排除依赖目录、字节码目录及 Mobile 历史 `release/acceptance-*` 快照。`nvidia[-_ ]?news|nvidianews|personal-noncommercial` 零命中，`rg` 返回 1 表示无匹配。单独遍历本仓被忽略文件后，旧实现、旧脚本名及旧字节码文件名同样零命中。

全工作树的旧实现名命中已分类：只保留历史证据、归档资料、`.recovery-artifacts/` 以及恢复副本；上述 pytest 缓存是唯一额外命中，已清理。Mobile 历史验收快照和 `.maintenance-backups/` 不作为当前源码，也不改写。现行 DSA 合同中指向旧来源删除记录的链接属于退役说明。

迁移规格的全部可见入链、出链解析成功；本轮主要规格、任务、指南及历史规格的 33 个相对链接检查无失效。两仓本轮已跟踪修改路径的 `git diff --check` 通过；新文件另行核对空白与链接。

## 数据保护、边界与回滚

未编辑历史新闻、业务数据库、来源启停、准入记录、当前政策、冻结 JSON 回执、历史迁移清单、Mobile 验收原件或恢复备份。主仓两份既有冻结 JSON 回执与 HEAD 比较相同。未创建提交、未改写其他并行任务的代码与配置。

本次只改变宿主维护工具及说明，目标服务部署、实源读取和浏览器验收不适用；已有来源退役的运行态证据仍由原任务持有，不由本次离线测试重新认证。

回滚仅需恢复本轮通用工具、消费者及文档的定向修改；无数据库或政策迁移需要回滚。测试缓存可自然重建。应保留工作树中其他既有或并行改动，不能整体重置仓库。

## 最终审查

AC1–AC3 的工具、消费者、现行文档、历史适用性与验证结果一致。当前执行面无旧来源支持、旧文件名调用或失效导入；历史资料保留可追溯性。本次源码清理范围完成，无未满足的验收条件。
