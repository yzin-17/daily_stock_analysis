# 沪深 ETF 来源与富途日线任务

规格：[沪深 ETF 日线来源核对与富途接入](../specs/2026-11-11-futu-cn-etf-daily.md)。

## 当前状态

本轮已完成。富途新增沪深 ETF 三口径日线，沪深日线总目录由 41 增为 44 个；三个新绑定均 `ready`。当前账号的沪市前／后复权、深市前复权完整窗口均 28／28、无缺日假设。1170 项相关测试及门禁通过，正式快更退出 0、两份生产文件与目标哈希一致。真实策略仍为修订 197，旧任务保持，本轮仅三次实源窗口。其他来源的口径和适配边界已分层记录，详见[证据](../../evidence/2026-11-11-futu-cn-etf-daily.md)。

## 执行项

- [x] F1：现有来源矩阵及边界，对应 AC-01。
  - 读取当前完整能力目录、Futu SDK 请求接缝与其他主要来源的 ETF／价格口径声明。
  - 所有权为本轮证据，不修改其他提供方的转换或准入合同。
- [x] F2：富途精确绑定与历史读取接入，对应 AC-02。
  - 所有权：`data_provider/thesis_ledger_sources/futu.py`、`data_provider/futu_history.py` 及对应测试。
  - 完成条件：沪深 ETF 三种日线口径通过真实工厂／Worker／SDK 受控接缝；静态身份、源失败、分页失败仍拒绝，既有股票范围保持。
- [x] F3：目标实源与最终核对，对应 AC-03、AC-04。
  - 依赖 F2 本地通过；按实际变更使用 Infra `sync-code.sh dsa`。
  - 完成条件：最多三次真实固定窗口、真实控制状态不变及源码匹配，成功与账号／数据限制分别记录。

## 输入与证据入口

- `data_provider/futu_history.py`、`data_provider/futu_daily.py`、`data_provider/futu_market.py`
- `tests/test_futu_daily.py`、`tests/test_thesis_ledger_futu_capability_path.py`
- `scripts/local-runtime/probe-backtest-daily-sources.sh`
- 既有来源基线：`thesis-ledger/docs/evidence/2026-11-11-backtest-source-compatibility.md`
- 详细记录：`docs/evidence/2026-11-11-futu-cn-etf-daily.md`

工作区既有其他任务与未提交修改保留；本任务不负责重建业务数据、改变主备选择或重跑历史回测。

## 最终一致性核对

AC-01 至 AC-04 均有输入、实现与证据。原始行情与前复权、上游支持与本地绑定、目录就绪与实际账号读取分别记录；未实读来源没有得到可用结论。当前合同由[富途接入说明](../../design/thesis-ledger-futu-openapi.md)维护，本轮无剩余执行义务。未发布镜像、未提交或推送。
