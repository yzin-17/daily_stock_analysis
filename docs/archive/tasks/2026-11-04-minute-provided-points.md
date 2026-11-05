# 分钟派生周期指标任务

规格：[分钟派生周期指标输入](../../specs/2026-11-04-minute-provided-points.md)。主任务：[第4～15项交付](../../../../thesis-ledger/docs/archive/tasks/2026-11-04-todo-4-15-delivery.md)。

- [x] C1 扩展provided-points周期声明，约束分钟市场及复权；保留原生周期、指纹及公式。
- [x] C2 定向验证旧周期、四种分钟周期及失败边界。
- [x] C3 独立进程HTTP与主仓消费者核对，记录输入与结果。
- [x] C4 主仓目标同步与客户端验收、复核完成后归档；不得用本仓单测替代。

## 最终验收

状态：已完成。独立uvicorn四周期及能力HTTP、篡改指纹422和TS解析通过；正式目标、真实富途聚合指标及Desktop浏览器完成，主仓验收已接续收口。 详见[主仓最终证据](../../../../thesis-ledger/docs/evidence/2026-11-05-todo-4-15-final-acceptance.md)。
