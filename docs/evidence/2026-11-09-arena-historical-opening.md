# 历史开盘来源校验

产品范围及当前状态由主仓[规格](../../../thesis-ledger/docs/specs/2026-11-09-cmb-historical-arena-replay.md)和[任务](../../../thesis-ledger/docs/tasks/2026-11-09-cmb-historical-arena-replay.md)持有；完整来源与运行态结果见[主证据](../../../thesis-ledger/docs/evidence/2026-11-09-cmb-historical-arena-replay.md)。

本仓 `src/services/arena_historical_opening_evidence.py` 是不联网的离线校验器，读取已捕获的真实不复权日线、日历开盘、快照与分钟旁证、上交所完整停复牌返回及当日公司概况；同时验证已复核规则正式原件和暂缓附件摘要。当前上市概况不扩大为其他历史日期，未知、停牌、错配和不完整覆盖均拒绝。输出仅供初始现金账户买入的事后回放，不认证实时执行或卖出结算，不回填材料可用时间。

`--bundle <文件> --output <文件>` 以独占创建方式发布冻结资格包；`--bundle <文件> --verify <资格包>` 按原资格时点重新校验全部输入并逐字段一致比较，不重新取数。消费侧维护入口在数据变更前强制执行该核验。规则适用校验与撮合费用分开：费用保留原比赛的明确模拟模型，不宣称为完整客户账单或法定收费事实。

`python3 -m unittest tests.test_arena_historical_opening_evidence -q` 七项通过，包含来源原件、日期、价格、状态、完整性及可用时间拒绝场景。新增文件已进入 `tests/data-test-scope.json`，范围校验通过。本次在宿主执行，不修改 DSA 服务 API、来源准入、运行依赖或镜像，没有部署此模块为在线能力。不存在同主题英文镜像文档，本中文证据与 CHANGELOG 承接变更。
