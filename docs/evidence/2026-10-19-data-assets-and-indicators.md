# 数据资产与指标保全证据

对应 [Task D2d/D2e](../archive/tasks/2026-10-18-data-only-service.md) 与 [D0 逐路径盘点](2026-10-19-data-service-dependency-inventory.md)。

## D2d：股票索引

股票索引已保全到 `src/data/assets/stocks.index.json`。原 Web 文件和数据资产均为 3,343,710 字节，SHA-256 同为 `1a7531e8f6ee3305c84e3e93f3bded469cb03eda0efb03706be5f7b2a4880725`，复制保留原修改时间，避免仅因复制改变“最新可用候选”的选择。原 Web 文件保留到 D4b。

后端种子候选改为数据资产；既有远端 URL、`data/cache/stocks.index.json`、远端格式校验、时效比较和 `static` 回退逻辑保持。两个索引生成脚本改写入数据资产，刷新入口从数据资产同步既有 `static` 位置；CSV 输入、JP/KR 种子与格式保持。未运行真实抓取或覆盖活动缓存。

| 检查 | 命令/输入 | 结果 |
| --- | --- | --- |
| 加载、远端优先级/失败回退、生成和刷新 | `.venv/bin/python -m pytest -q tests/test_stock_index_loader.py tests/test_stock_index_remote_service.py tests/test_generate_index_from_csv.py tests/test_refresh_stock_index.py tests/test_data_stock_index_asset.py` | 94 项通过；2 条既有依赖弃用提示 |
| 无客户端目录 | 临时根目录只放真实数据资产；实际加载 A 股、JP/KR 名称；临时 CSV 运行真实生成函数并同步静态兼容位置 | 通过；没有 `apps` 目录 |
| lint | `.venv/bin/python -m flake8 tests/test_data_stock_index_asset.py tests/test_refresh_stock_index.py src/data/stock_index_loader.py scripts/refresh_stock_index.py --count --statistics` | 0 |

D9a 仍须把数据资产纳入独立镜像/载荷检查；D4b 删除前复核全部产品引用。这里没有执行删除或目标部署。

## D2e：纯指标内核

`src/data/technical_indicators.py` 拥有 MA/MACD/RSI 纯函数。正式序列直接消费它们，不再创建 `StockTrendAnalyzer` 或导入旧 Config/决策 Schema；原生三个方法暂时委托同一内核，保留类属性自定义周期与返回列。股票趋势产品的其他行为未重构。

修改前冻结 `tests/fixtures/data_indicators_legacy_v1.json`，输入源码 SHA-256：`src/stock_analyzer.py` 为 `d35ea0561a74d80ab9bcdf371b23eb253a31d7846983f357c349eeee6164130b`，正式序列为 `d7f40ade3322a9ac3ab214e5ccbb334de5a2ba6d51d53d904d59c0b3edefb390`。8 组输入涵盖空/短/平价/上涨/下跌/混合/缺值/自定义周期；保存全行数值摘要、末行数值、三种正式输出摘要和输入指纹。数值按 12 位小数归一化，非有限值记 null；冻结过程网络尝试为零，没有从新内核重建期望。

| 检查 | 命令/输入 | 结果 |
| --- | --- | --- |
| 固定旧基线、原生兼容、周期与既有流水线 | `.venv/bin/python -m pytest -q tests/test_data_indicator_kernels.py tests/test_stock_analyzer_rsi.py tests/test_stock_analyzer_bias.py tests/test_thesis_ledger_indicator_calculation.py tests/test_thesis_ledger_period_indicators.py tests/test_pipeline_realtime_indicators.py` | 74 项通过；5 条既有依赖/收集提示 |
| 增量、预热和缺值 | 8 组固定全行输出；混合序列的 1/6/19/34/60/73 行前缀逐点核对；原输入不被修改 | 通过；保持短序列 MA60 回退、EMA/RSI 种子及正式预热屏蔽 |
| 无产品/SDK 导入 | 新解释器阻断旧 Config、StockTrendAnalyzer、决策 Schema、模型 SDK、Provider 和 socket 后运行真实三种正式序列 | 通过 |

本叶保持既有算法身份，不替代 ThesisLedger IR09/IR10 的版本、可见性或目标运行验收。
