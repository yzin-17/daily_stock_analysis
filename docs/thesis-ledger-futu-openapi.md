# ThesisLedger 富途 OpenAPI 接入

完整范围与产品验收由主仓 `docs/specs/2026-10-08-futu-openapi-full-integration.md`、对应 Task 管理；本文拥有 Provider 端接口盘点及实际适配说明。

## 连接与当前适配

沿用数据源页面的 OpenD 主机地址和端口。当前开发连接 `host.docker.internal:11111`；具体部署使用自己的 OpenD 地址。凭据不包含券商密码或交易密码。

已验收的每手股数仍仅覆盖港股股票，见[每手股数接口](thesis-ledger-security-lot-size.md)。新增精确快照当前覆盖港美股票/ETF：先查询静态资料核验证券类型，再调用单标 `get_market_snapshot`；关闭上下文、进程总预算 4.5 秒，不使用历史收盘价补当前价。代码测试与真实行情验收分开记录，未完成的下列接口不显示为可执行数据源能力。

用户已确认无需账户与交易，本任务仅使用 `OpenQuoteContext`。`SECURITY_STATIC_INFO` 新增港美股票/ETF 独立路由，按规范证券代码和类型读取当前静态资料，返回字符串证券 ID、每手股数、名称、上市日期及当前退市标志等字段；未知字段保持未知，不接受历史参数，不自动写入目录。Data Contract 入口为 `/api/v3/thesis-ledger/market/security-static-info`，局部测试、真实读取与页面分别记录。

## 官方行情清单与实施归属

2026-10-09 核对[官方 Python 行情总览](https://openapi.futunn.com/futu-api-doc/quote/overview.html)：119 个方法、9 个推送处理器。当前安装 `futu-api 10.8.6808`；119 个方法均可在该版本的 `OpenQuoteContext` 或继承的基础上下文中找到，`get_global_state` 位于基础上下文。方法存在不能证明所有市场、权限或最低 OpenD 版本已满足。实际调用继续逐接口核对参数和版本。

| 责任任务 | 方法或处理器 | 接入状态 |
| --- | --- | --- |
| LOT、FAPI-6a1～3 | `get_stock_basicinfo` | 港股股票每手股数已验收；港美股票/ETF 当前静态资料四类产品 API 通过，腾讯与 SPY 页面实读通过；其他资产及全市场列表待接入 |
| FAPI-2/3 | `get_market_snapshot` | 港美股票/ETF 实际产品 API 与来源检查通过；价格详情页尚待验收 |
| FAPI-4/5 | `request_history_kline` | 港美股票/ETF 三种复权实际 API 读取通过；图表页面及冻结重放尚待验收 |
| FAPI-6a | `get_referencestock_list`, `get_ipo_list` | 待接入 |
| FAPI-6b | `get_global_state`, `request_trading_days`, `get_market_state`, `get_rehab`, `get_history_kl_quota` | 待接入 |
| FAPI-6c | `get_plate_list`, `get_plate_stock`, `get_owner_plate` | 待接入 |
| FAPI-7a | `get_stock_quote`, `get_rt_ticker`, `get_rt_data`, `get_order_book` | 待接入 |
| FAPI-7b | `get_broker_queue`, `get_cur_kline` | 待接入 |
| FAPI-7c | `subscribe`, `unsubscribe`, `unsubscribe_all`, `query_subscription` | 待接入 |
| FAPI-7c | `StockQuoteHandlerBase`, `OrderBookHandlerBase`, `CurKlineHandlerBase`, `TickerHandlerBase`, `RTDataHandlerBase`, `BrokerHandlerBase` | 待接入 |
| FAPI-8a | `get_option_expiration_date`, `get_option_chain`, `get_option_screen`, `get_option_volatility`, `get_option_exercise_probability`, `get_option_strategy`, `get_option_strategy_spread`, `get_option_strategy_analysis`, `get_option_quote` | 待接入 |
| FAPI-8a | `get_option_market_statistic`, `get_option_underlying_overview`, `get_option_underlying_his_statistic`, `get_option_underlying_his_volatility`, `get_option_underlying_rank`, `get_option_rank`, `get_option_event`, `get_option_zero_dte_screener`, `get_option_zero_dte_contract`, `get_option_earnings_screener`, `get_option_seller_screener` | 待接入 |
| FAPI-8b | `get_warrant`, `get_warrant_screen`, `get_future_info` | 待接入 |
| FAPI-8c | `get_capital_flow`, `get_capital_distribution`, `get_top_ten_buy_sell_brokers`, `get_daily_short_volume`, `get_short_interest` | 待接入 |
| FAPI-8d | `get_financials_earnings_price_move`, `get_financials_earnings_price_history`, `get_financials_statements`, `get_financials_revenue_breakdown`, `get_research_analyst_consensus`, `get_research_rating_summary`, `get_research_morningstar_report` | 待接入 |
| FAPI-8d | `get_valuation_detail`, `get_valuation_plate_stock_list`, `get_company_profile`, `get_company_executives`, `get_company_executive_background`, `get_company_operational_efficiency` | 待接入 |
| FAPI-8e | `get_shareholders_overview`, `get_shareholders_holding_changes`, `get_shareholders_holder_detail`, `get_shareholders_institutional`, `get_insider_holder_list`, `get_insider_trade_list` | 待接入 |
| FAPI-8e | `get_institution_list`, `get_institution_profile`, `get_institution_distribution`, `get_institution_holding_change`, `get_institution_holding_list`, `get_ark_fund_holding`, `get_ark_stock_dynamic`, `get_ark_active_transaction`, `get_rating_change` | 待接入 |
| FAPI-8f | `get_corporate_actions_dividends`, `get_corporate_actions_buybacks`, `get_corporate_actions_stock_splits`, `get_dividend_calendar` | 待接入 |
| FAPI-8g | `get_earnings_calendar`, `get_macro_indicator_list`, `get_macro_indicator_history`, `get_fed_watch_target_rate`, `get_fed_watch_dot_plot`, `get_economic_calendar` | 待接入 |
| FAPI-8h | `get_stock_filter`, `get_stock_screen`, `get_search_quote`, `get_search_news`, `get_earnings_beat_rank`, `get_dividend_rank`, `get_us_pre_market_rank`, `get_us_after_hours_rank`, `get_us_overnight_rank`, `get_top_movers_rank`, `get_hot_list`, `get_short_selling_rank`, `get_period_change_rank`, `get_high_dividend_soe_rank` | 待接入 |
| FAPI-8h | `get_industrial_chain_list`, `get_industrial_chain_detail`, `get_industrial_chain_by_plate`, `get_industrial_plate_info`, `get_industrial_plate_stock`, `get_heat_map_data`, `get_rise_fall_distribution`, `get_indicator_list`, `request_indicator_calc_async`, `IndicatorCalcHandlerBase` | 待接入 |
| FAPI-9 | `set_price_reminder`, `get_price_reminder`, `get_user_security_group`, `get_user_security`, `modify_user_security`, `get_option_event_alert`, `set_option_event_alert`, `PriceReminderHandlerBase`, `OptionEventHandlerBase` | 待接入 |

SDK 还有总览未列出的兼容别名/内部连接钩子及其他方法；它们不自动成为产品能力。官方接口清单更新时先对账再新增明确契约。其他市场/资产、订阅和写入均由对应任务承担，不能由当前单标快照测试推导通过。

## 运行与验证

本地行为覆盖使用 `tests/test_futu_market.py`；当前 Provider 清单、精确报价目录与真实运行时分发必须一致。运行态通过同级 infra `scripts/sync-code.sh dsa` 同步代码；镜像固化需 `scripts/update.sh dsa`。独立主备选择不自动改变已有用户策略。

静态资料消费者位于主仓“市场数据 → 标的目录 → 当前证券资料”，需要用户在“路由策略”选择对应的 `SECURITY_STATIC_INFO` 来源。2026-10-09 四类实际样本：`00700.HK` 100 股/手、`02800.HK` 500 股/手、`AAPL.US` 1 股/手、`SPY.US` 1 股/手；这些是当前返回值，不能证明历史变更。SPY 的废弃 `listing_date` 字段返回 `1970-01-01`，页面标为供应商未核验日期，未替换成自行推断的上市日。实际回执位于主仓忽略目录 `.recovery-artifacts/futu-2026-10-09/static-info/`；验收临时路由已按 CAS 复原，不改变原价格选择。

SDK 对量额、缺失时间及错误响应的来源说明以[快照字段](https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html)为准。快照更新时间是当地市场时间，美股需要考虑夏令时。OpenD 与 SDK 调用失败不能保存成有效零报价。
