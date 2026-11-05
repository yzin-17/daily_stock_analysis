# DSA DG2c 故障恢复与正式镜像往返证据

> 2026-10-20（Asia/Shanghai）；DG2c 通过。[原 Task](../../archive/tasks/2026-10-18-data-only-service.md)的 D10s 拥有维护入口，DG2c 拥有本次目标验收。[原始报告](2026-10-20-dg2c-recovery.json)保存逐阶段、逐表、逐对象摘要。

## 保护与入口

沿用已验证的 DG2a 正式备份及 [DG2b](2026-10-20-dg2b-retention.md) 逐对象基线。infra `scripts/dsa-target-recovery.sh` 持有正式维护锁，复用已有 `DsaRuntimeRecovery.snapshot/restore`，固定本地 context、`.env` 摘要、不可变镜像、API 命令、端口和全部原挂载。目标往返前核对正式备份与逐对象基线一致；切换后发生异常时，由 `finally` 恢复已登记的候选快照。没有构建、拉取镜像、清库或删除业务卷。

原兼容基线为 `sha256:ffba40664b7f59baab8c575ffef7f14c3a899ed49672156e692aac7c39385477`，候选为 `sha256:06b6054ba433de813e1bfc407872b4068ed9d9650946143c19dd5d3147c6f389`；两者均以原 `uvicorn server:app` API 命令运行。旧基线按登记快照核对，当前候选另须满足 `dsa-data-runtime-v1` 标签；没有把候选标签要求降低为仅健康。

候选快照为 infra 私有 `.maintenance-backups/dsa-runtime/1792430937578-06b6054ba433.json`。快照不保存环境明文；全部原数据、匿名日志及报告卷均沿用，回退期间未新增业务挂载。

## 隔离保留副本的实际故障

维护入口只创建本轮有唯一身份的候选容器：无外网、非 root、只读根目录、仅 tmpfs 状态和三个只读测试资料绑定，不挂任何业务卷。将正式备份中的 SQLite 和 189 个原数据文件复制到该专属副本；测试使用合成配置及合成加密键，不复制真实环境凭据、不发起来源或模型请求。

`tests/test_data_process_recovery.py` 在原数据副本上新增受控任务，真实启动 Python 进程并在持久化屏障后发送 SIGKILL：

- 目录：中断任务租约过期后保留明确 timeout，且只生成一个替代任务；替代任务成功。旧 owner 的晚到提交不发布新 generation，再次启动不增加任务或 generation。
- 公告：重启后领取同一任务的第二次 attempt，owner 已改变，旧领取不能提交；成功后不重复领取。另一任务在三次租约尝试后终止为 failed，不无限重试。
- OAuth：副本中新建一个受控待处理会话，中断后由真实 Manager/Store 的启动恢复变为 failed / OAUTH_RESTARTED，再次恢复为 0。原目标 OAuth 基数仍为 0；没有实际授权、浏览器或 token 刷新请求。

目录成功发布沿用现有五代缓存策略，副本中较早的 **1 代目录缓存**按既有策略清理。逐代摘要证明只删除低于新 generation−4 的条目；另有反例证明该许可不能掩盖较新缓存或其他业务行丢失。副本中其余原记录及 189 个原文件保持。该受控增量及清理只发生在隔离副本，真实目标三阶段的原记录/文件无增量、无删除。

同次执行既有公告/目录的停止和真实 provider 子进程回收、OAuth Manager/API 关闭、数据 lifespan 等断言；共 **35 项通过，0 失败、0 错误、0 跳过**。最终活跃子进程为 0，额外线程为空。JUnit SHA-256：`9915e155f27fe963aac481c99c7120e5823afeef7c7811645167943e894cf4b8`，完整日志/XML 保存在私有结果目录。

## 实际目标往返

在 infra 执行，退出 0：

```sh
rtk proxy bash scripts/dsa-target-recovery.sh /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dg2a-20261019-1 /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dsa-runtime/1792425319093-ffba40664b7f.json /Users/yzin/code/thesis-ledger-workspace/daily-stock-analysis/docs/tasks/evidence/2026-10-20-dg2a-target-acceptance.json /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dg2b-20261020-1/report.json /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dg2c-20261020-5
```

北京时间 01:28:45～01:29:54 完成全部步骤，未重新构建镜像：

| 阶段 | 镜像 | 原表/原记录 | 原文件 | 实际业务读取 |
| --- | --- | --- | --- | --- |
| 往返前 | `06b6054ba433` | 43 / 225 | 189 | Control V3 及既有 FX 缓存通过 |
| 恢复兼容基线 | `ffba40664b7f` | 43 / 225 | 189 | Control V3 及既有 FX 缓存通过 |
| 恢复候选 | `06b6054ba433` | 43 / 225 | 189 | Control V3 及既有 FX 缓存通过 |

每阶段均实际比较原列/原行内容和每个原文件，新增记录与文件均为 0；原库完整性通过。FX 请求使用原缓存的 rateDate，读取真实 V3 API，核对货币方向、日期及汇率，无冷获取。主仓七类保护表、schema head、57 个成功 V3 Run 及结果校验和均与更新前一致。往返后完整 DG2b 再次通过，详见留存证据。当前已恢复候选，所有本轮隔离容器清理完成。

## 已修复问题与定向验证

实际恢复预检发现原快照的 Docker 默认 HostIp 为空字符串，旧序列化错误地生成 Compose 不接受的 `host_ip: ""`。现在对默认地址省略该字段，核对时仍保留默认地址与显式地址的差异，未扩大监听范围。默认地址、0.0.0.0、127.0.0.1、IPv6 及地址漂移拒绝均有回归。

本地 27 项恢复/资源边界单元、4 项正式入口、4 项进程/审计断言通过；11 项留存对账回归通过。新增数据测试已进入显式范围清单，D0 冻结库存及其摘要未变。首次四次尝试分别停在端口预检、macOS tar 附加元数据、缺少只读测试清单及合法缓存清理的对账规则，均未切换目标；失败证据保留。后续仅修复维护装配与断言，复用同一候选镜像；没有为每场景重复构建或修改业务计算。

成功报告 SHA-256：`f53b14212c409a1aca744859c52166d7303dbc647e47b11fa89033c7aacc24c3`。入口、恢复实现、端口快照、留存程序及进程测试的实际 SHA-256 均在报告 `scripts` 中；报告和证据以成功执行时输入为准。所有失败/成功输出、备份和候选快照保留在 infra 私有忽略目录，未提交业务数据。

真实模型保持暂停，来源/准入/路线/凭据未更改。DG3 仍未通过：只读核对确认目标无已配置新闻来源及 NEWS_SEARCH 路线，用户指定记入原 Task 的 TODO；本次不代替真实新闻消费。
