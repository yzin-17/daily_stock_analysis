# Tushare 回包校验与错误分类任务

规格见[回包校验与错误分类](../specs/2026-11-27-tushare-endpoint-resilience.md)。

## 当前状态

- [x] T1 错误分类与账号检查。具名 V1 在上游拒绝后能按下一次实际响应恢复读取。账号检查与业务请求结果独立，分组限频及草稿隔离回归通过（AC1、AC2）。
- [x] T2 回包边界与业务投影。畸形信封、错列、字段异常、精确筹码错标的或错日期均被拒绝，合法空结果与跨仓 Schema 回归通过（AC3）。

## 验证

在基线 `5fcd4791` 的 DSA 隔离工作树执行 Tushare、账户检查、精确筹码、日线与报价相关离线回归，共 658 项通过。跨仓消费复用主仓已有 Schema 产物。语法、静态与数据合同检查通过。

上述 658 项为本地 DSA 代码和离线合同验收。

## 合入与部署

`65127177` 已快进合入并推送到 `origin/yzin`，与行情来源批次同次快更。目标容器健康，Tushare HTTP 客户端文件校验值与宿主一致。该批次的部署明细和远端 CI 见[行情读取兼容与来源校验任务](2026-11-27-upstream-market-reader-followups.md#合入部署与目标验收)。该批次的真实数据请求范围为 TickFlow ETF。

## 目标凭证复验

- 2026-11-27 17:55（中国标准时间），通过目标 DSA 的只读控制快照使用当前凭证（凭证版本 1、配置版本 5），诊断请求固定到官方 HTTPS 地址，保留现行服务端点配置。`daily` 对 `600519.SH`、`hk_daily` 对 `00700.HK` 各返回 2026-11-25 的 1 行，证券代码、日期和所请求字段均匹配。[Tushare HTTP 协议文档](https://tushare.pro/document/1?doc_id=130)
- 同一天的 `cyq_chips` 返回 HTTP 200 和业务码 `40203`，拒绝文案带接口名。原解析把这类回包记为 `unknown/upstream_failure`。`6eb43168` 识别该权限文案，并保留未知 `40203` 的错误分类和现有逐接口处理。86 项 HTTP、账号权限与精确能力定向回归通过。
- 18:05 快更后，容器内 `data_provider/tushare_http_client.py` 与宿主 SHA-256 同为 `8c320167fa9c2af07af580fa6ca3f6e8c5fb8cb50db1d50887292dd369e432a7`。对 `600519.SH`、2026-11-25 的 `cyq_chips` 单次真实复验再次收到 `40203`，现正确记录 `permission_missing/permission_denied`。凭证版本和配置版本与首轮一致。快更和镜像验证见[行情读取兼容与来源校验任务](2026-11-27-upstream-market-reader-followups.md#目标镜像更新与运行验证)。
- 18:08 `6eb43168` 的完整远端门禁通过，包含 3 个后端离线分片、治理、Docker 构建及镜像冒烟。[GitHub Actions 记录](https://github.com/yzin-17/daily_stock_analysis/actions/runs/45978001690)
