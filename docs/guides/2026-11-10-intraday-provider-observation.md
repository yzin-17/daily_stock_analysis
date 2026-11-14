# 盘中来源只读采样

产品契约及实施任务由[主仓规格](../../../thesis-ledger/docs/specs/2026-11-10-routing-recommendation-and-intraday-probe.md)和[任务](../../../thesis-ledger/docs/tasks/2026-11-10-routing-recommendation-and-intraday-probe.md)管理；来源读取说明见[DSA 专题](../thesis-ledger-intraday-observation.md)。

## 入口与时段

从 infra 根目录运行；省略 `--execute` 只展示计划，不创建文件或请求来源。

```bash
node ../daily-stock-analysis/scripts/local-runtime/intraday-provider-observation.mjs --phase=asia --output=/绝对路径/asia
node ../daily-stock-analysis/scripts/local-runtime/intraday-provider-observation.mjs --phase=us --output=/绝对路径/us
```

中港固定为北京时间2026年11月11日10:05、10:15、10:35、11:05；美股固定为当日23:05、23:15、23:35和次日00:05。执行模式按时等待四批，等待分段不超过60秒，单批子进程上限十分钟。机器及 Codex 应保持运行；错过窗口十五分钟后记漏采，不顺延至其他交易日。

## 目标与恢复

只接受本地 `thesis-ledger-dev` 项目的运行中 `dsa` 容器；拒绝覆盖 `DOCKER_HOST`，记录容器身份并在读取后复核。通过 `dsa:dsa` 执行 DSA 只读模块，不更新策略、凭证、准入或数据库，不启动和重建容器。

每批先以排他创建写入请求许可，再运行读取并写脱敏 JSON 回执。已有成功回执复用；有许可无回执或已有未知结果时停止，禁止自动重跑扩大查询预算。每批传递累计回执，来源及市场共用十二次逻辑查询上限；明确受限后停止对应读取。

宿主输出目录权限700、文件600，应放在主仓忽略的 `.recovery-artifacts`。异常只保存稳定状态和错误类型，不保存异常原文或 Docker 检查的完整环境信息。计划完成后停止，不自动实施缓存调整。

## 离线验证

```bash
node --test ../daily-stock-analysis/scripts/local-runtime/intraday-provider-observation.test.mjs
```

受控时钟与响应验证固定日期、跨日时段、累计预算、重复执行和未知结果停止；实际盘中验收独立保留，不能用离线通过代替。
