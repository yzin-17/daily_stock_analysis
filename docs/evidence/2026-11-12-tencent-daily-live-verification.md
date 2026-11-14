# 腾讯日线当前绑定真实验收

最新接续：共享合同收敛及缓存首次成功代次修复后，完整门禁8074通过、1跳过、4取消选择、323个子测试通过。缓存任务正式sync-code.sh dsa退出0，DSA于2026-11-12T05:21:44.735475006Z启动；只读复核腾讯原生模块SHA256仍为fab2735f110ecd6c002bdbab03b87c7c0ab5f5dab333f50b7b80624199fe7e9f，与下文12:20真实读取输入一致。正式更新及门禁回执见[主仓完成阶段证据](../../../thesis-ledger/docs/evidence/2026-11-12-market-base-result-cache-completion.md)。未新增腾讯请求；下文历史失败、原启动代次和当时未部署的结论保留为各自时点事实。

关联[规格](../specs/2026-11-11-tencent-daily-response-window.md)及[任务](../archive/tasks/2026-11-11-tencent-daily-response-window.md)。用户2026年11月12日要求“无视之前的授权次数限制”，此前次数上限已解除；历史账目保持，供应商明确限制、资格和数据保护边界继续有效。

## 真实结果

北京时间12:20:23～12:20:24，当前DSA目标`providerId=tencent`、`upstreamSource=tencent`，绑定修订`dsa-v3-stock-tencent-none-adapter-v2`。通过`ReadOnlyProbeStore -> candidates -> read_entry -> CAPABILITY_SOURCES`读取600519.SH股票2026-11-12不复权日线，结果成功。

一条逻辑请求、一次requests调用，无重定向、自动重试或切源，公开行情费用0。HTTP200、业务码0、原包装匹配；原始641行，覆盖2024-03-21～2026-11-12。请求数量640与兼容响应上限641分别保留，未放宽保护或截断原始响应。

实际参数：`_var=kline_day2026`、`param=sh600519,day,2026-11-12,2026-11-12,640,`、`r=0.8205512681390605`。适配器严格裁剪后只有2026-11-12一根日线，`verify_transport`验证一段成功；分段起止均为2026-11-12，rows=1、partitionComplete=true。

| 字段 | 实际返回值 |
| --- | --- |
| 时间 | `2026-11-12T00:00:00` |
| 开盘／最高／最低 | `1395.13`／`1411.0`／`1388.11` |
| 收盘字段 | `1409.4` |
| 成交量／成交额 | `1995200`／`2789770000` |
| 原始确认／适配器完成标识 | 均为`null` |

数值保持原适配器投影。“收盘字段”是当前日柱close，不认证当日最终收盘价。本轮仅一次腾讯读取，不能推断更新频率、只返回确认柱或缓存TTL；不将上午AKShare与本次腾讯混作同来源变化证据。

## 日历与运行身份

实际Server的`MarketSessionReader`于12:20:23返回CN `state=closed`、`tradingDayState=open`，nextBoundaryAt为13:00北京时间；这是已知交易日午休。修订为`market-period-calendar-v1:b8ee89ea4f47db8701b0a89e78abb045ea623b3590fcdfcf5407f4aef67f8ac5`。本次是独立午休日线验证，不冒充10点自动化或交易时段内采样。

DSA读取前后身份一致：容器`f43ca681b9fdf8db70d1624fe77f69bde28a8e835f423b43cf514d7bc2188b98`，镜像`sha256:f34796cac189c0b9866f8278273b2b690f57f71b2144f782eb99eae48e9740f4`，启动`2026-11-11T17:58:21.6605615Z`。目标及宿主`data_provider/tencent_native_daily.py`的SHA256均为`fab2735f110ecd6c002bdbab03b87c7c0ab5f5dab333f50b7b80624199fe7e9f`。修复已存在，本轮没有重复部署；正式更新过程及全仓门禁仍独立核对，不能据源码一致就勾选全部AC4。

未改路由、凭证、启停、准入、Redis、TTL或业务数据，未交易、提交或推送。没有新增其他来源请求。

## 入口与原始回执

DSA所属入口为`scripts/local-runtime/verify-tencent-daily.mjs`及Python载荷，复用目标已有精确读取模块。默认零请求计划；显式执行、独占许可、日期／日历／源码资格核对均在读取前完成；结果未知不自动重发。

在infra目录执行：

```sh
node ../daily-stock-analysis/scripts/local-runtime/verify-tencent-daily.mjs \
  --date=2026-11-12 \
  --output=/Users/yzin/code/thesis-ledger-workspace/thesis-ledger/.recovery-artifacts/tencent-daily-verification-20261112 \
  --execute
```

退出0，`status=success`、`providerReads=1`、`targetUnchanged=true`。忽略目录权限700、文件600，保留许可、会话及完整公开响应；不记录凭证或认证头。

- `report.json` SHA256：`45f0bf3224f168aa545fa2b531f63a062369fe6a771ff55093c130dfa60d916a`。
- 原始响应67686字节，SHA256：`9c590820e085149ae03b6513b483ee1bc661379c2eef100d0dfd3dfc117924f9`。
- 传输修订：`39b5ddd399de96a6ac26332feb93c88a5e6792677d291c3a37d62035a828b1c4`。

## 验证与分层结论

当前宿主命令`.venv/bin/python -m pytest -q tests/test_tencent_native_daily.py tests/test_tencent_daily_window.py`为45通过；`node --test scripts/local-runtime/verify-tencent-daily.test.mjs`为3通过，覆盖默认零请求、参数拒绝、未知日历及许可去重、已知午休状态保留。载荷`ast.parse`及flake8关键错误检查退出0。

完整门禁只运行一次，使用仓库`./scripts/ci_gate.sh`。语法、关键flake8及确定性检查通过，离线套件最终34失败、8013通过、1跳过、4取消选择、323个子测试通过，耗时294.75秒，退出1。JUnit的8048个主条目及tests=8371分别保留，不混淆主测试和子测试计数。日志和JUnit位于同一忽略目录`ci.log`、`ci.xml`，JUnit SHA256为`ce6044e43a15d7f7e4dd002031a4cef3aef01e360856a02d6168aaa9407ba2aa`。

34项中两项为`test_adjusted_daily_capability_audit.py`腾讯后复权请求字典遗漏了既有SDK辅助参数`r`。保留变量名、精确日期、复权、数量和量额断言，仅补齐规格已要求的`r=0.8205512681390605`，该文件随后24项全部通过；没有修改业务源码或重发腾讯请求。该补丁发生在全仓结果之后，不将定向通过伪称新的全仓通过，也不把“推算还剩32项”写成一次实际全仓结果。

其余已失败断言属于新闻准入及密钥Schema、研究／基础行情资格（包括腾讯后复权是否属于基础行情的独立分类）、目录／上市／分钟周期、公共费率／规则、独立HTTP期望及Dockerfile安装断言。完整名称和异常保留于JUnit；本次只修改腾讯辅助参数对应的两项断言，其他失败不删改、不降门槛，也未通过回滚隔离证明其全部属于历史基线。因此L3.G及缓存D2仍未通过。共享工作区输入已变化，不能把此前23项与本次34项差值解释成本轮引入数量。

本地／受控与当前代表真实绑定均通过，原始确认语义仍未知。其他标的、复权、周期、完整部署、缓存和浏览器未由本次验收覆盖。实际成功不补造正式更新过程证据，L3其余门禁继续由活动任务持有。

最终写集为DSA只读验收入口三文件、上述审计测试的一处参数断言、原腾讯Spec／Task、当前独立证据及跨仓状态索引。业务适配器本轮未改，三仓WIP保留，未新增自动化。后续需在对应领域断言收敛后重验全仓，并取得01:58更新的正式入口回执和适用源码快照；不为补造部署证据再次同步当前共享环境。

最终一致性核对通过：两仓定向`git diff --check`退出0，目标文档引用文件均存在、无行尾空白，真实回执摘要未变且仍仅一条请求。已勾选L3.V有独立真实证据，L3.G／L3.D及缓存门禁保留未勾选；旧次数限制已在当前Spec／Task中明确解除，历史账目与当前授权没有混用。
