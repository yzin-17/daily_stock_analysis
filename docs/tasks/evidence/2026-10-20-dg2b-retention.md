# DSA DG2b 目标数据与历史留存证据

> 2026-10-20（Asia/Shanghai）；DG2b 通过。沿用[原 Task](../../archive/tasks/2026-10-18-data-only-service.md)与[DG2a 候选](2026-10-20-dg2a-target-acceptance.md)，[原始报告](2026-10-20-dg2b-retention.json)记录逐表及逐对象摘要。

## 输入与只读边界

正式基线为 infra 私有目录 `.maintenance-backups/dg2a-20261019-1/`：原 `dsa.sqlite`、`dsa-data.tar`、主库和回测文件备份的字节及 SHA-256 全部验证通过。目标仍为 DG2a 的不可变镜像 `sha256:06b6054ba433de813e1bfc407872b4068ed9d9650946143c19dd5d3147c6f389`；正式 `.env` 摘要、原数据/日志/报告挂载及主仓保护状态与更新前一致。

新增 infra `scripts/dsa-state-retention.mjs` / `.py`：基线使用只读 SQLite 副本和不落地解包的 tar 清单；目标通过 `mode=ro` 连接取得内存快照，对原列的逐行摘要、每个原文件内容分别比较。报告仅保存摘要、数量及对象指纹，原路径映射保存在私有忽略目录的 `baseline.json`。目标读取真实解密/凭据格式、FX、公告原件与任务函数，禁止出站连接，不初始化或补写活动业务数据。读取完成再次核对逻辑状态和文件，确认验收期间没有变化。

定向验证为 `rtk proxy ../daily-stock-analysis/.venv/bin/python scripts/dsa-state-retention.test.py`：11 项通过；覆盖记录改写/删除、相同文件数的丢失/替换、原列比较、增量单独计数、秘密不进入摘要、只读拒写及路径/链接越界。Node 语法及 Python 静态检查通过。

## 实际目标结果

在 infra 执行，退出 0：

```sh
rtk proxy node scripts/dsa-state-retention.mjs audit /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dg2a-20261019-1 /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dsa-runtime/1792425319093-ffba40664b7f.json /Users/yzin/code/thesis-ledger-workspace/daily-stock-analysis/docs/tasks/evidence/2026-10-20-dg2a-target-acceptance.json /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dg2b-20261020-1
```

执行时间为北京时间 2026-10-20 01:08:27～01:08:29；无数据库整体哈希相等的前提：

| 对象 | 结果 |
| --- | --- |
| SQLite | 43 张原表，225 条原记录，原列及逐行内容一致；完整性通过 |
| 原文件 | 189 个，9,363,848 字节，每个内容摘要一致；新增、缺失、变更均为 0 |
| 凭据 | Futu `opend`、Hithink `api_key` 两条既有密文均由目标当前密钥环解密及真实格式校验通过；凭据版本保持 1，明文未输出或持久化 |
| FX | 1 条原缓存，经真实仓储查询函数完成正向及反向读取；1 条缺少可信时区的原抓取时间继续不返回 `fetchedAt`，未补造时间 |
| 公告 | 21 条记录的原件引用均由真实 Repository 读取并校验 SHA-256；37 个原任务均为 `succeeded`，94 份查询缓存可解析 |
| 目录 | 14 个历史任务保持原状：12 `succeeded`、1 `failed`、1 `timeout`；未把历史失败改写成成功 |
| 旧产品业务 | 原聊天、报告、账户等表的零记录基数逐表保留；报告目录存在且 0 文件 |
| OAuth | 原会话表 0 行；原 home 未持有文件，候选中该空目录不存在且文件仍为 0。未声称有真实存量 OAuth 凭据或文件恢复正例 |
| 主仓与运行态 | 七类保护表、结构 head 与成功 V3 Run 清单保持；容器、启动时间、镜像和挂载在验收前后不变 |

原始报告 SHA-256：`1e8e7d4ff7fcbd2b27b93daf21595abb5be592040312d1824ab0c68c94d64a2c`。私有基线与报告保留在 `.maintenance-backups/dg2b-20261020-1/`，未导出秘密或把业务备份纳入 Git。

本叶只完成留存和读取。其后 [DG2c](2026-10-20-dg2c-recovery.md) 单独通过受控中断/恢复及正式镜像往返。北京时间 01:31:03～01:31:05 再以同一入口执行恢复后完整对账，43 表、225 原记录、189 文件、2 条凭据及 FX 读取继续通过；报告存于 infra `.maintenance-backups/dg2b-20261020-after-recovery/report.json`，SHA-256 为 `dc44721f91ae649d02bca8a583971d6b32e4b9f3823ef0aeebe05709cb20b8ed`。原始首次报告与其摘要保持。DG3 的真实新闻消费继续未完成，真实模型继续暂停。
