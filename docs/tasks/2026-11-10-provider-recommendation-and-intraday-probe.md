# 来源推荐与盘中采样任务

规格：[本仓契约](../specs/2026-11-10-provider-recommendation-and-intraday-probe.md)。跨仓状态由主仓同名任务维护；本仓保留本仓验证结果。

## 当前状态

代码、离线门禁与目标运行态验收已通过。中港下午补采、美股四批和跨市场汇总均已交付。原定时履约及来源覆盖仍为部分通过，由主仓 AC6 保存原证据和停止条件。

- [x] D1：只读采样工具、比较器及受控验证。
- [x] D2：来源推荐元数据、目录修订与兼容测试。
- [x] D3：移除本地主动等待和计数，验证错误及资源保护。
- [x] D4：定向、包级、边界和运行态验证；完整更新退出0，运行中只读预检完整、32个候选、0次上游读取。
- [x] D5：按用户2026-11-11“现在验收”的指示完成中港下午四批；104次逻辑查询、49成功、55失败或未覆盖，各来源每市场不超过12次。上午漏采、原始确认标识缺失及中途容器重启分别记录，不认证所有来源可用。[验收记录](../../../thesis-ledger/docs/evidence/2026-11-11-cn-hk-intraday-observation.md)。
- [x] D6：美股四批真实采样与汇总已交付。24 次逻辑查询，18 成功、6 失败，两来源各 12 次。复用[原采样及汇总回执](../../../thesis-ledger/docs/evidence/2026-11-11-us-intraday-observation.md)，保留晚间启动、首两批间隔和事前会话证据缺口。

2026-11-22 对账仅更新执行状态，复用原报告和离线证据。既有 `./scripts/ci_gate.sh all` 通过7746项、323子用例，1项跳过、4项排除。输出 `/tmp/intraday-dsa-gate-accepted.log` 仅适用于当时输入。首次全量暴露旧TickFlow样本实际联网，已修为受控工厂，偏差详见[主仓证据](../../../thesis-ledger/docs/evidence/2026-11-10-routing-recommendation-and-intraday-probe.md)。

`sync-code.sh all` 的DSA兼容预检拒绝，尚未同步即退出；改用 infra `./scripts/update.sh dsa` 完整更新成功，再通过 `sync-code.sh thesis-ledger` 同步主仓成功。DSA、Server和Worker均健康，目录契约及浏览器四宽度复验通过。原跨仓验收仍保留未通过的历史时点，接续条件见[主仓任务](../../../thesis-ledger/docs/tasks/2026-11-10-routing-recommendation-and-intraday-probe.md#接续与停止条件)。
