# 本地收费资料按需刷新验证记录

规格见[按需刷新](../specs/2026-11-12-local-rule-demand-refresh.md)，当前状态只由[任务](../tasks/2026-11-12-local-rule-demand-refresh.md)维护。主仓此前第 1 批 HTTP 422 回执继续保留，本文不替换历史失败，也不重启已经停止的 12 批行情／规则验收。

## 实现与受控验证

`HISTORICAL_PUBLIC_FEES` 的统一本地入口与原始具体来源均接入刷新。日期向后超出覆盖时复用登记目录、有限文档读取、原正文版本记录和收费解析器。完整集合相对资料包 `refreshBaseline` 没有新增／变更时，末段费用的已核验覆盖延长至市场当地检查日；扩展投影单独保存于 `rule-source-monitor.sqlite` 的 `rule_material_coverage_v1`，不修改原 JSON 或已冻结结果。

首次资料、正文变更及未登记的 PDF／DOCX 引用属于待处理资料。空白正文、验证码、重定向、限流、未读完和控制／资料包变化都不能认证无变化。失败冷却 300 秒；覆盖写入前的持久化占用防止监测任务已结束但覆盖尚未提交时重复抓取。向基线以前或市场未来日期的查询不出站。

最终定向组包含 20 项：CN／HK／US 股票和 ETF 保留各自解析器，已核验范围重开复用、完整无变更、新资料不能通过重复抓取洗成无变更、附件引用、失败冷却、并发占用、市场时区及晚到控制变化。使用真实 HTTP 抓取器与正文解析器、`httpx.MockTransport`、隔离 SQLite，以及带认证的实际 Data 请求链；这些输入不证明真实现行费率。

相关回归范围为本地资料选择、费用能力／HTTP 路由、监测运行／存储／抓取／正文／调度／历史、ETF 和原股票公共收费。命令：

```sh
.venv/bin/python -m pytest tests/test_local_rule_demand_refresh.py tests/test_thesis_ledger_local_materials.py tests/test_thesis_ledger_public_fees_capability_path.py tests/test_thesis_ledger_public_fee_routes.py tests/test_rule_monitor_runtime.py tests/test_rule_monitor_store.py tests/test_rule_monitor_fetch.py tests/test_rule_monitor_snapshots.py tests/test_rule_monitor_schedule.py tests/test_rule_monitor_history.py tests/test_thesis_ledger_etf_public_fees.py tests/test_thesis_ledger_public_fees.py -q --tb=short
.venv/bin/python -m flake8 src/services/local_rule_coverage_store.py src/services/local_rule_demand_refresh.py src/services/thesis_ledger_public_fees_query.py tests/test_local_rule_demand_refresh.py
.venv/bin/python scripts/check_data_service_boundary.py --syntax
```

最新相关回归 137 项通过（3.25 秒），其中新增定向 20 项；定向 flake8 通过，数据服务边界／语法检查为 486 个运行文件、1 个已审查动态导入且语法通过。早期 130 项、补齐市场路径后的 136 项亦曾通过，不累计为额外测试数量。既有 FastAPI／httpx／AnyIO 弃用与 `_ThreadlessTestClient` 收集警告未改变。

补充市场路径测试最初暴露美股当地仍为前一日的受控时钟设置；修正测试时钟并加入市场未来日零抓取断言，生产读取也提前拒绝未来日期。最终并发测试还覆盖了监测结束、覆盖投影提交前的窗口，持久化占用保证不重复抓取。

## 意外真实文档读取与修复

早期相关回归中的 `test_pin_identity_and_window_are_not_silently_changed` 查询基线以前的日期，意外触发一次 CN 来源检查，共 6 次真实官方文档 GET。没有行情或模型调用，没有新增费用；资料包和覆盖终点均未改写。已向用户披露，并补上旧 HTTP 测试的默认网络隔离及基线以前的提前拒绝。后续刷新测试全部显式注入受控传输。

真实监测任务 `5bf0ba95-0545-4d6b-80af-6129e9241eeb`：`succeeded`、文档 6、`changed=0`、不可用 0；覆盖结果 `updates_pending`、投影为空。`changed=0` 只是监测没有既有版本可比较，不是旧费率已获当前认证。以下为当时实际记录的规范正文指纹；原测试临时数据库已被 pytest 保留策略清理，不能声称这些旧正文已随本文持久化。

| 当时读取地址 | 规范正文 SHA-256 |
| --- | --- |
| https://www.hengqin.gov.cn/lab/flfg/fl/content/post_3517835.html | `c1a6f256863be2a016dfca960d20df4788b46a363ae27f89cca9581a00467b32` |
| https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448 | `9ec4e672b81e6bf6c6e8994c75966b6ffece7edd8fc1b93bb2236e868040f024` |
| https://www.sse.com.cn/lawandrules/sselawsrules/repeal/rules/c/c_20230818_10776219.shtml | `4e02eb5ea99d6bf1c551fa9cce478f58c275723267034707b7f474e6b1a57812` |
| https://www.sse.com.cn/lawandrules/sselawsrules2025/charge/c/c_20250610_10781461.shtml | `138d40c22a47ffa6eb61bf8e3a6d3cce3bf30030e14bc493c1984b65338c41da` |
| https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/201806/t20180627_960950_ext.html | `4516c1e906c629dac3baa8bfb938224725bad7678297e919eb85cfdb549ffad7` |
| https://jrj.sh.gov.cn/SCDT197/20220429/f715759a877b4158812eb6df70ccb49e.html | `b670a72de7fc8762cc2a3d75622a8b511c07c787e858d4052a82096dace2f827` |

取得的页面中含废止通知与现行指南引用，尚未完成费项提取、附件核对和资料包绑定基线。首次资料不能仅以 HTTP 200 或后一次正文相同认证为没有规则变更。本次额外 6 次公开资料读取独立于此前已经停止的第 1／12 批 Data 规则验收，不存在自动恢复剩余 11 批。

## 当前证据边界

已从 infra 执行 `./scripts/sync-code.sh dsa`：兼容预检和正式仅代码更新成功，目标 `thesis-ledger-dev-dsa-1` 健康，容器 ID `f43ca681b9fdf8db70d1624fe77f69bde28a8e835f423b43cf514d7bc2188b98`，镜像保持 `sha256:f34796cac189c0b9866f8278273b2b690f57f71b2144f782eb99eae48e9740f4`。随后只读检查 15 个本次生产文件的 SHA-256，全部与宿主一致；容器内刷新模块和覆盖存储模块导入通过。该只读检查没有来源请求、模型调用或数据库写入。仅代码更新位于容器可写层，不能当作镜像发布，容器重建后会消失。

本地合同和受控链路可证明按需刷新及覆盖持久化；当前真实资料包缺少已核验的 `refreshBaseline`，首次新资料仍待解析和采纳。主仓 T06.2 继续拥有现行费用依据、独立实时规则与 ETF 执行证据；部署和真实当前日期读取另行验收。未提交代码，保留两仓原有未提交修改。
