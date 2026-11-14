# 缓存前置门禁合同收敛证据

对应[规格](../specs/2026-11-12-cache-prerequisite-gate-reconciliation.md)及[任务](../archive/tasks/2026-11-12-cache-prerequisite-gate-reconciliation.md)。本轮只收敛缓存前置合同，不执行真实来源或部署，不改变真实路由、凭据、缓存和业务数据。

## 基线与合同归属

从上一轮完整JUnit提取未由腾讯辅助参数断言修复的20个文件，在当前输入定向复现：32失败、228通过，64.50秒。日志为主仓忽略目录`.recovery-artifacts/cache-completion-20261112/failed-scope-baseline.log`及同名XML。原完整34失败和两项腾讯定向修复不改写。

对照主仓能力首次使用规格、DSA WEB_SEARCH独立路由规格、按需收费覆盖规格、实际精确注册表及统一安全错误投影逐组判断。修改对象为旧测试假设／受控输入，不通过改实现迎合旧快照；本轮未修改业务源码。部分测试还需先修正前面的旧假设才能执行后续精确目录断言，后续暴露的Baostock ETF等缺项同样按实际注册表完整收敛。

## 新闻、搜索与首次使用

七文件定向：AKShare、HiThink、Yahoo新闻，新闻来源／快讯，能力首次使用和公开搜索，共114项通过。范围包括：

- 当前精确能力在明确启用并选源后可首次尝试，不再依赖历史人工单标准入窗口；未选路由仍零调用。
- 完整公开目录核验仍必须成立，未知标的、伪造游标和旧凭据游标零调用；新凭据重新请求采用新上下文。
- 旧时间窗口可取得空的部分结果，保持覆盖不完整，不伪造历史可见性；部分结果仍为unverified。
- 搜索来源分别登记NEWS_SEARCH与WEB_SEARCH，默认关闭和独立功能路由继续校验，未将新闻授权自动迁移至网页搜索。

测试命令：`.venv/bin/python -m pytest -q tests/test_akshare_news.py tests/test_hithink_news.py tests/test_yahoo_news.py tests/test_thesis_ledger_news_sources.py tests/test_thesis_ledger_news_feed.py tests/test_capability_first_use_verification.py tests/test_thesis_ledger_public_search.py`。

## 目录、周期与研究资格

对应十文件组其余121项通过，修正后的完整Control目录文件11项随后通过。保持显式研究模式、精确来源、市场／资产／标的／周期一致性；已支持的none／hfq作为正例，真正不支持的复权作为负例，没有删除资格失败门禁。

精确登记核对为CN日线48、US16、HK14，共78；富途日线为三市场、两资产、三复权18个组合，原30个分钟组合保持。腾讯股票hfq、富途CN ETF及BaoStock ETF既有实现纳入精确集合。目录verification字段单独严格验证为unverified，再比较事件来源结构；不因能力目录可选推断真实账号权限或当前数据覆盖。

读取控制策略中的不支持复权负例改为现仍不支持hfq的Yahoo股票精确绑定，保留旧V2拒绝、V3控制合约及unsupported_adjustment原因断言。没有改变实际路由。

## 资料覆盖、错误与构建

公告、港美规则、公共收费主备、统一错误信封及Docker静态合同七文件71项通过。覆盖不足明确为422／insufficient_coverage，原资料损坏为502／invalid_response，均读取统一detail.error；未改成来源不可用或权限不足。

港美越界测试采用各自市场合法、但不在固定包内的证券，避免用无市场后缀的uncovered同时测试路由市场推断与资料覆盖。独立数据进程的ETF负例改用股票代码600519.SH，验证真实资产不匹配；原510300.SH目前已有合法研究覆盖，不能继续当作无效请求。

Docker测试继续要求pip缓存挂载和requirements安装，允许BuildKit缓存选项中的既有id，不修改Dockerfile或构建镜像。测试命令为上述七个文件的分组pytest；独立物理运行态另行7项全部通过，57.27秒，回执为同一忽略目录`physical-runtime.xml`。

## 当前层级

上述合同收敛没有重新发起腾讯、其他Provider或官方资料网络请求。公共收费旧回归继续注入禁止出站的文档读取器；真实HTTP刷新由已有隔离测试拥有。未写真实准入或清理其他WIP。

## 完整门禁与接续边界

使用仓库`./scripts/ci_gate.sh`执行完整数据门禁。首轮8065通过、1跳过、4取消选择、323个子测试通过，310.94秒，退出0。随后缓存任务D1b修复首次成功展示记录误改控制代次，生产输入变化后再次执行完整门禁：8074通过、1跳过、4取消选择、323个子测试通过，316.82秒，退出0；共享工作区同时存在其他WIP，测试数量差异不全部归因于本任务。

日志分别为主仓忽略目录`.recovery-artifacts/cache-completion-20261112/ci.log`、`ci-first-use.log`及同名XML；最终XML SHA256=`3e1db57df7c9539e70e4c0e8fdd68bc7f7819214a7d995cebfde6d10c7ecb16c`。原32失败／228通过基线及历史34失败继续保留，不改写为通过。

G1～G6验收完成。后续正式DSA快更、实际隔离Redis和浏览器由[主仓缓存证据](../../../thesis-ledger/docs/evidence/2026-11-12-market-base-result-cache-completion.md)持有；本门禁完成不代替Server目标部署或真实供应商资格。
