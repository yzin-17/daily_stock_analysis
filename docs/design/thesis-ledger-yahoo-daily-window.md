# Yahoo 日线完整窗口

## 来源与合同

`data_provider/thesis_ledger_sources/yfinance.py` 拥有 Yahoo 在线前复权绑定，SDK 请求使用固定标的、明确起止日期及 `auto_adjust=False` 读取原始 OHLC 与 Adj Close，组装后复用当前 SDK 的 `utils.auto_adjust` 生成前复权。返回列、市场日期、复权、分页及实际会话继续经过生产校验，缺少原始份额或权益依据时保持未知。

在线绑定采用适配修订 v2、协议 `dsa-yfinance-exact-daily-bounded-window-v2`。`yahoo-gme-share-window` 与 `yahoo-hk-share-window` 是独立静态参考窗口，保留原 v1 协议及范围，不进入在线补取流程。

## 末日补取

Yahoo 的整段窗口响应可能缺末日收盘，即使较小窗口有该日数据。SDK 复权会进一步将该日其他 OHLC 转为空值。来源 helper `data_provider/yfinance_daily_window.py` 对唯一末行空价格执行至多一次补取：

1. 其余行完整，末日至少三项原始 OHLC 和成交量已知，且原请求不只包含这一个缺价日。中间／多日缺价、缺少同日对照、错误身份、乱序或重复日期直接拒绝。
2. 补取仅请求缺价日，标的、上游及原生字段口径保持；不使用通用 `repair=True`，也不再请求已经验证无效的两日窗口。
3. 补取的该日原有原始价格与成交量必须一致；价格比较仅容纳 `1e-10` 相对或 `1e-12` 绝对浮点计算误差，不用于容忍实际口径差异。
4. 仅用该日真实返回填原空价格／Adj Close，原有原始价格、成交量、窗口及其他行保持；随后由 SDK 一次完成前复权，不自行推断因子。缺少有效价格、冲突及超时继续拒绝。

两个 SDK 调用每次仍最多 4.5 秒，聚合最多 8 秒，第二次同时受剩余预算约束；剩余预算不足不补取。未触发缺价时只有一次调用，既有 `run_probe` 外层 8 秒截止保持。SDK 权限、认证处理与单次超时保持，不通过自动循环增加请求次数。

## 读取证据与观察

读取帧的 `yahooDailyRetrieval` 记录原窗口、最终复权口径、两次原生 SDK DataFrame 摘要、补取日、同日对照字段及填充字段，算法修订为 `yahoo-daily-bounded-tail-window-v2`。摘要范围为 `sdkDataframe`，不声称原始 HTTP 字节。生产 `verify_transport` 复核该证据，分页 `pagesFetched` 如实为一或二；单改页数或伪造不完整证据不能通过。前复权仍为既有的 provider-defined 语义，不认证统一锚点或严格 PIT。

`scripts/local-runtime/probe-backtest-daily-sources.sh` 的 `--inspect-yahoo` 仅允许唯一 Yahoo 目标；在同一次请求内观察原始 Chart JSON、SDK 复权前／后与本地规范化前的公开字段缺失统计。观察器不改返回数据、不增加网络请求，异常原文、Cookie、凭证和价格值不进入输出。

固定窗口真实证据、测试及部署边界见[排查记录](../evidence/2026-11-11-yahoo-daily-price-missing.md)。历史结果不自动变为成功，当前准入、路由和真实业务任务独立保持。
