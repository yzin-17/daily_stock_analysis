# ThesisLedger 每手股数与富途连接

## 已接入能力

`SECURITY_LOT_SIZE` 为香港股票当前每手股数的独立主备路由。候选为 `longbridge/longbridge` 与 `futu/futu`，用户自行选择顺序或只选一个；不更改日线、报价路由，不隐式回退到东方财富。

富途本轮只提供静态资料，不登记报价、K线、持仓或下单能力。长桥原有报价／日线能力保持，每手股数读取复用现有授权和静态资料缓存。

## 配置步骤

1. 在运行 OpenD 的电脑上启动并登录 OpenD，确认监听地址、端口和数据权限。
2. ThesisLedger 数据源页面选择“富途 OpenAPI”，配置 `host`（OpenD 主机地址）和 `port`（实际监听端口），保存并启用。通过现有配置存储保存连接信息，不接收券商账号密码或交易密码。
3. DSA 运行在本机 Docker 时，应填写从容器可达的主机地址，例如 `host.docker.internal`；不要把容器内的 `127.0.0.1` 当成宿主机。端口以 OpenD 配置为准，不需要把服务暴露到公网。
4. 在“港股／股票／每手股数”中选择主源与备用源。长桥需要先配置现有 OAuth 或 Legacy 凭证并启用。
5. 回测的每手股数编辑器点击“按路由查询当前每手股数”，核对来源、观察时间和采用值，再应用并重新确认。查询失败时可保留已有研究预设并手动覆盖，不编造缺失默认值。

## 读取边界

富途使用 `OpenQuoteContext.get_stock_basicinfo(Market.HK, stock_type=STOCK, code_list=[...])` 的 `lot_size`，不创建交易上下文、不查询持仓。SDK 请求放在限时子进程中，单次最多 4.5 秒；进程返回只包含标准化股数或安全错误。正常读取与异常读取均关闭行情上下文。

长桥使用 `QuoteContext.static_info()` 的 `lot_size`，要求标的和港股股票类型一致；沿用静态资料实际缓存观察时间。两个来源按各自连接／凭证与标的隔离，默认缓存 24 小时；长桥 TTL 沿用已有配置。缓存和最新值不回填旧回测快照。

标准化结果含标的、正整数每手股数、公开接口引用、来源、观察时刻及 SHA-256。摘要指向标准化观察，不冒充交易所公告原字节；当前接口不是历史整手查询，始终标记 `historicalChanges=unverified`。

## API

DSA：`POST /api/v3/thesis-ledger/market/security-lot-size`，使用已有 Data Token。请求字段为 `contractVersion=3`、`consumer=thesis-ledger`、`requestId`、`symbol`。服务器按当前生效的独立路由选择主备；不接收历史日期或复权参数。

产品：`GET /api/market-data/security-lot-size?symbol=00700.HK`。返回实际 `routeTarget`、策略修订、观察与返回时间。OpenD 地址和凭证不进入数据响应、模型或诊断导出。

## 核验范围

适配器、真实 Control 配置／主备执行、Data Token HTTP、Server 解析、参数编辑和旧预设兼容有定向测试。自动测试使用受控 SDK 替身，不代表真实券商已连接。实际配置状态和部署核对由主仓 LOT Task 记录；没有可用 OpenD 或长桥授权时，不声明真实读取成功。

官方字段说明：[富途静态资料](https://openapi.futunn.com/futu-api-doc/quote/get-static-info.html)；[长桥静态资料](https://open.longbridge.com/docs/quote/pull/static)。
