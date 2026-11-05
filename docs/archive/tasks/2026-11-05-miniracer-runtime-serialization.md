# MiniRacer原生运行时串行化任务

规格：[MiniRacer运行时串行化](../../specs/2026-11-05-miniracer-runtime-serialization.md)。本任务是[行情交付](../../../../thesis-ledger/docs/archive/tasks/2026-11-04-market-chart-followups.md)的运行时恢复前置，主目标继续有效。

- [x] J1 入口提前安装同步MiniRacer进程锁，保留类身份、异常和可选依赖语义。
- [x] J2 受控并发／重入／幂等与宿主实际JS进程通过。
- [x] J3 按运行条件选择infra正式入口恢复，目标Linux ARM64并发JS及业务Control验证。
- [x] J4 记录原生失败、恢复结果，回到主仓分钟与盘中验收；复核后归档。

J2：新测试已登记至data-test-scope.json，受控并发／异常及既有分钟指标共34项通过；宿主mini-racer 0.14.1、8调用线程／24个实际JS实例通过，来源读取0。初次update.sh dsa因失效venv Python在构建前退出；采用既有LONG_BRIDGE_SOURCE_PYTHON参数再次调用时被并发维护锁拒绝，均未绕过门禁。后续观察原DSA恢复为running且仍无守卫，按当前代码变更及运行状态改用sync-code.sh dsa。各入口结果分别保留，J3后续正式恢复和目标验证已完成。

## 最终验收

状态：已完成。按容器实际状态选择正式入口：两次update在构建前失败／锁拒绝后，容器恢复running且纯代码条件具备，正式sync-code.sh dsa退出0。目标Linux ARM64 mini-racer 0.14.1的24个真实JS上下文／8线程通过；Control、真实Yahoo／富途读取恢复。守卫摘要3adfe6361c7bf8b654718c596b739dfe794c0b498c10a7454df446bdc8e5c823，后续同步输入未变。 详见[主仓最终证据](../../../../thesis-ledger/docs/evidence/2026-11-05-todo-4-15-final-acceptance.md)。
