# Yahoo 日线价格缺失任务

规格：[价格缺失排查与修复](../specs/2026-11-11-yahoo-daily-price-missing.md)。

## 当前状态

本轮分析、修复与目标验收已完成。Yahoo 多日原始响应缺少末日 Close／Adj Close，单日响应完整；最终通过同日原始字段核对及单日补取恢复，再由 SDK 前复权。股票 `600519.SH` 与 ETF `510300.SH` 的固定完整窗口均实际补取后通过，均 28 条／28 会话、无缺日假设。1074 项相关测试及门禁通过，正式快更退出 0，两个生产文件与目标哈希一致。实际 8 次逻辑诊断、12 次数据 SDK 调用；原修订 197 与历史失败任务保持。详见[证据](../../evidence/2026-11-11-yahoo-daily-price-missing.md)。

## 执行项

- [x] Y1：来源分层观察，对应 AC-01。
  - 所有权：`scripts/probe_backtest_daily_sources.py`、其测试与维护入口；复用既有只读真实库、临时副本与生产读取。
  - 输入：固定标的与完整窗口、已启用 Yahoo 绑定、已安装 yfinance 转换函数。
  - 完成条件：转换前／后与本地规范化前的缺值统计清晰，观察器不改变返回数据或扩大请求次数，真实控制状态保持。
- [x] Y2：实现确定的最小适配修复，对应 AC-02。
  - 依赖 Y1。主要写集为 Yahoo 日线适配、`data_provider/yfinance_daily_window.py` 及其测试；不修改公共价格、覆盖或回测门禁。
  - 完成条件：正常窗口以及错误身份、缺失原始价格／复权依据等拒绝场景均有确定性验证；数据源限制保留明确证据。
- [x] Y3：目标运行态与最终核对，对应 AC-03、AC-04。
  - 依赖 Y2 本地验证通过，或 Y1 证明无可实施本地修复。
  - 完成条件：按实际写集通过正式同步入口、完整窗口实读及源码核对；记录实际次数和未验证范围，不创建业务回测。

## 执行入口

- `data_provider/thesis_ledger_sources/yfinance.py`
- `data_provider/exact_daily_contract.py`
- `scripts/probe_backtest_daily_sources.py`
- `scripts/local-runtime/probe-backtest-daily-sources.sh`
- `tests/test_thesis_ledger_yfinance_capability_path.py`
- `tests/test_backtest_daily_source_probe.py`

详细观察与验收证据见 `docs/evidence/2026-11-11-yahoo-daily-price-missing.md`。当前合同由 [Yahoo 日线完整窗口](../../design/thesis-ledger-yahoo-daily-window.md)维护。

## 最终一致性核对

- AC-01 至 AC-04 均有证据，初版两日补取及错误聚合时限的失败保持原记录，未计入成功。
- 最终两个 SDK 请求每次最多 4.5 秒、聚合最多 8 秒，原诊断外层 8 秒截止保持；同日价格、成交量和来源／覆盖校验保持。
- 目标实读和受控测试分开记录；未新建业务回测、未改主备或历史，未发布镜像，未提交／推送。
- 代码、Spec、长期设计与证据按最终实现对账，未验证的其他标的／时段不获得成功结论，本任务无剩余执行义务。
