# 富途与 HiThink 筹码账号核查

## 结论与授权边界

2026-11-09 核查当前项目账号。HiThink 筹码入口拒绝当前 Key；富途现有认证为 OpenD，不能用于官方筹码 REST 接口。用户随后确认“暂不新增认证”。本轮没有满足完整筹码摘要接入条件的新来源，既有 AKShare／东方财富路由保持生效。

本报告是固定核查记录；完整任务、验收断言及最终审查见[主仓任务](../../../thesis-ledger/docs/archive/tasks/2026-11-09-chip-provider-expansion.md)。

## HiThink：实际平台、合同与账号

项目 HiThink 使用扶摇平台 `fuyao.aicubes.cn`，认证为 `X-api-key`，不是 iFinD 的 `quantapi.51ifind.com` 平台。

读取当前控制配置中的凭据，来源为 `control`、方式 `api_key`、配置版本 9。使用同一 Key 分别只读请求：

| 请求 | 实际返回 | 能证明的事实 |
| --- | --- | --- |
| `/api/a-share/prices/snapshot?thscodes=600036.SH` | HTTP 200、业务码 0、1 条记录 | 当前 Key 可以读取正常报价 |
| `/api/a-share/special-data/chip-shape-dates` | HTTP 200、业务码 2004，拒绝 | 当前 Key 未能取得筹码形态日期 |

[官方筹码形态文档](https://fuyao.aicubes.cn/docs/api-reference/chip-shape/)明确写明“该能力暂未开放外部接入”，页面仅介绍同花顺 AI 客户端内的数据契约。其接口输出是可选日期、形态股票筛选及分页，没有项目 `ChipResult` 必需的平均成本、获利比例及 70%／90% 成本区间。因此未新增该来源的筹码摘要绑定。只有官方开放兼容摘要合同且当前 Key 真实取数通过，才具备后续接入条件。

## 富途：现有 OpenD 与 REST 的区别

当前控制配置来源为 `control`、认证方式 `opend`、配置版本 1。已安装 SDK 为 `10.08.6808`；其 `OpenQuoteContext` 未发现名称含 `chip` 或 `cyq` 的接口，结合[官方 OpenD 行情总览](https://openapi.futunn.com/futu-api-doc/quote/overview.html)，未找到筹码读取入口。

[官方 REST 筹码接口](https://open.futunn.com/zh-cn/api/quote/chip/quote-chip-distribution)为 `GET /api/v1.0/quote/{symbol}/chip-distribution`，文档含平均成本、获利比例、70%／90% 区间和价格持仓量分布，主要覆盖 A 股。[认证文档](https://open.futunn.com/zh-cn/api/overview/getting-started)要求独立 OAuth Bearer 或 AppKey 签名，不能把 OpenD 的主机与端口作为 REST 凭据。

当前项目没有相应 REST 认证。只读的未认证对照请求 `/api/v1.0/quote/SH.600036/chip-distribution` 返回 HTTP 401、业务码 -12006；它只记录未认证请求被拒绝，不能判定当前富途账号的筹码权限或覆盖。用户明确暂不新增认证，本轮停止认证扩展。未取得当前账号真实筹码结果，未新增可用目录或适配绑定。

## 可重复诊断与数据保护

新增 `scripts/probe_chip_provider_accounts.py`，复用当前凭据解码和选择规则，但通过 SQLite `mode=ro` 与 `PRAGMA query_only` 读取；不运行控制库初始化或凭据轮换，不保存凭据、不改写配置或路由。结果仅输出认证元信息、数值状态、公开字段名及条数，舍弃原始错误文本、令牌和请求头。HTTP 请求设置连接 3 秒、读取 6 秒超时并禁止跳转。

本次在主仓目录执行只读命令：

```sh
rtk proxy docker exec -i thesis-ledger-dev-dsa-1 python -B - < ../daily-stock-analysis/scripts/probe_chip_provider_accounts.py > /tmp/chip-provider-account-probe.log 2>&1
```

退出 0。脱敏结果行以 `CHIP_PROVIDER_PROBE=` 开头，保存在上述宿主机日志中。本次未执行 Docker 状态变更，不向容器复制脚本，不部署未验证的适配器。

独立读取 Server 策略确认：版本 193、`syncState: applied`、共 117 条路由，筹码仍为 `CN / STOCK / CHIP_SUMMARY -> akshare / eastmoney`。

## 验证与限制

- 四项诊断脚本测试通过，覆盖错误响应脱敏、成功字段白名单、网络异常脱敏，以及 SQLite 只读约束和跳过初始化。命令在 DSA 根目录执行：`UV_INDEX_URL=https://pypi.org/simple uv run --no-project --with pytest --with requests python -B -m pytest --noconftest -q tests/test_chip_provider_account_probe.py`。
- `--noconftest` 仅用于独立运维脚本单元测试；现有 `.venv` 的 Python 链接已失效，默认仓库测试入口另缺 `anyio`。本次通过 uv 隔离缓存运行测试，未更改项目依赖。测试环境出现 Python 3.9 LibreSSL 的 urllib3 提示；真实网络请求在既有 DSA 容器中执行。
- 两个新增 Python 文件的 `py_compile` 与定向 `flake8` 通过。未修改业务适配、Schema、API 或客户端，因此没有构建镜像、执行快更或扩大业务回归。
- 本次没有取得富途认证后的筹码数据，也没有 HiThink 完整筹码摘要的成功结果；两者均未被宣称为已接入或可用。

回滚仅需移除新增诊断脚本及对应测试、记录；现有运行态和持久路由不需要回滚。
