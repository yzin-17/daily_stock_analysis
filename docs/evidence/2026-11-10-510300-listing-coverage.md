# 510300 上市事实与前复权准备验收

## 问题与修复

ThesisLedger 的任务 `ed08b1ad-82b3-4769-82f7-095a1925b5b3` 于北京时间 2026-11-10 23:36:39 发起，标的为 `510300.SH`，前复权区间为 `2026-10-10` 至 `2026-11-10`。预热后的实际行情窗口为 `2026-09-24` 至 `2026-11-10`。

当前第 197 版路由选择主源 `hithink / fund-market-historical`、备用源 `akshare / eastmoney`。日志显示两次行情请求均返回 422。两个请求均能通过字段校验，但缺少本地已核验上市事实，且这两个精确绑定没有研究模式下的上市未知处理资格，覆盖上下文为空；接口在访问上游前返回 `insufficient_coverage`。

修复在既有上市事实库存中新增 `510300.SH` 的有来源记录：

- 首个交易日：`2012-05-28`。
- 来源：[南方东英基金管理人发布、港交所披露的产品资料概要](https://www.hkexnews.hk/listedco/listconews/sehk/2025/0804/2025080402003_c.pdf)，发布日期为 2025-08-04，第 3 页明确列出主 ETF 的上市日期及代码 `510300`。
- 修订：`hkex-csop-csi300-kfs-20250804-listing-510300`。
- 来源已公开日期：`2025-08-04`，沿用既有日期事实的零点时间表示。

没有更改路由、来源准入、研究模式资格或完整窗口门禁；没有改写旧快照。原未知上市测试使用仍无登记事实的 `510050.SH` 保持缺事实语义。

## 验证

在 DSA 仓库执行：

- `.venv/bin/python -m pytest -q tests/test_thesis_ledger_etf_listing_coverage.py tests/test_thesis_ledger_research_calendar.py`：34 项通过。
- 覆盖上市、交易状态、单位、日历、Data V3、HiThink ETF 适配及来源固定的相邻 10 个测试文件：201 项通过。
- `PATH=.venv/bin:$PATH ./scripts/ci_gate.sh syntax`：通过。
- `PATH=.venv/bin:$PATH ./scripts/ci_gate.sh deterministic`：能力边界、运行合同、数据测试登记及确定性测试通过。
- `git diff --check`：通过。

新增测试同步登记到 `tests/data-test-scope.json`。未运行整个 DSA 离线测试集，没有将局部结果扩展为全仓通过。

## 目标运行态

执行目录为 `/Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra`，正式入口及参数为：

```sh
./scripts/sync-code.sh dsa
```

兼容性预检通过，脚本退出码为 0，DSA 健康。镜像 ID 保持 `sha256:cbe278c43f707c4ce6deb9ce78b613691cd3e29cb1bbb1f4bdcf66b75fa40899`。该更新位于容器可写层，不是镜像发布，容器重建后需重新应用当前源码。

独立只读检查在目标容器确认新增事实修订已经生效，同一窗口能建立覆盖上下文，包含 28 个预期交易日。

## 实际请求验收

复用原失败任务的策略版本、标的、资金、前复权口径、执行模型和区间，生成独立请求标识及本次数据截止，调用真实 Server 的 `/api/v1/backtests/run-config/prepare`：

- HTTP 201，`status=prepared`。
- `executionPreflight.status=ready`。
- 实际来源为原路由的 HiThink 主源，未切到其他来源。
- 行情输入指纹为 `a13b1e751edf155c64fa9e0485d7bd1198c990b3ca6b0888218b7fb4659ced29`。
- 验收前后，原失败任务 API 响应完全一致。

脚本为 `/tmp/qfq-preparation-qa.cjs`，准备输入及响应保存在 `/tmp/qfq-preparation-qa.json`。本次验收证明原行情准备阻断已解除，没有创建新回测任务，未将准备通过表述为完整回测执行成功。
