# DSA DG2a 目标镜像与合同验收证据

> 执行跨 2026-10-19～20（Asia/Shanghai）；结论：DG2a 的正式镜像、原生 SDK、目标合同及受控消费通过。状态唯一入口为[原 Task](../../archive/tasks/2026-10-18-data-only-service.md)；[原始成功报告](2026-10-20-dg2a-target-acceptance.json)保持运行时原文。

## 执行范围

用户指定按 P1 优先完成 DG2a，继续主线程执行，保留数据，真实模型暂停。仅使用 infra 正式 `update.sh dsa` 更新目标；真实来源不因本叶自动启用，受控数据与实源证据分开。DG2b 的逐对象留存、DG2c 的故障恢复/正式回退、DG3 的新闻实源消费继续由各自叶持有。

既有应用源码结果沿 D9b 的原输入复用：6,178 项本地物理数据回归及主仓六类互通。本轮新增/修改的维护、镜像、CI 和验收输入另做定向与目标验证，不把旧计数当成当前镜像全量结果，也不累计重叠测试。业务计算及提供方合同未改，Provider 路由、准入和凭据未改。

## 更新前保护与回退组合

在 infra 使用已有入口：

```sh
rtk proxy node scripts/research-state-backup.mjs backup --target thesis-ledger-dev --output /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dg2a-20261019-1
```

退出 0，备份完成于 `2026-10-19T15:25:48.692Z`。备份目录为私有忽略目录，未把业务数据放入 Git：

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `postgres.dump` | 3,862,283 | `f2766c51619f97f888b1c309b27c3327e5591b9243183b6b004d5ca5c683990f` |
| `dsa.sqlite` | 27,070,464 | `40de7183b1d0c1f774407ce12d8cb8770bae2a2893f5c428bfab45becba75e04` |
| `dsa-data.tar` | 9,676,800 | `e37cd13ab90164397f4c4383b5e8fdfc5099df40274edf310688baf82d54bb09` |
| `backtest.tar` | 4,939,776 | `e0928461e9642b1b09d97cb14c69622760f4fc6fb850c6faabc7fb2b991b7348` |

初始 DSA 镜像为 `sha256:ffba40664b7f59baab8c575ffef7f14c3a899ed49672156e692aac7c39385477`；Server/Worker 均保持 P0 候选 `sha256:c1f3c9d890e17f6ffa827aae9c80a8b21fd53e4d37d08ed4a51a3f3de8187eac`。主库 head 为 `20261019020000_research_observation_versions`，活动回测为 0，成功 V3 Run 为 57。DSA 更新使用 `DEV_DATABASE_MODE=check`，未清库。

正式更新自动保存 `.maintenance-backups/dsa-runtime/` 中的原不可变镜像、命令、端口、全部挂载及环境摘要。原数据卷、匿名日志卷和匿名报告卷保持；原报告目录为空，OAuth home 无文件。回退入口为 `ENV_FILE=.env bash scripts/restore-dsa.sh <正式更新前快照.json>`，本叶登记该入口与本地拒绝测试，不把实际回退演练标为通过。

## 已查明并修复的问题

1. 正式入口在构建前拒绝锁归属：Node 启动器增加了一层进程，直接父进程不等于持锁 Shell。现场只读复现为 caller 与 ppid 不同；增加启动器层级的入口反例先失败，再以真实祖先链核对修复。他人锁、循环、无效 PID 和进程查询失败仍拒绝，17 项定向验证通过。未跳过锁或祖先检查。
2. 镜像完成依赖及定制 SDK 构建后，应用导入因 `ENV_FILE=/dev/null` 失败。配置合同拒绝已存在的非普通文件，此规则保持；Docker 构建、独立镜像验证、CI 分片及本轮装配改为自有普通空文件。相关 47 项定向通过，后续构建复用原生及依赖缓存。
3. 中间镜像 `sha256:82629a7d24b0f4f39ef1167150fe5e738eb820fa284b1e11c97bb7e214ff4f62` 已完成正式更新，但 DG2a 拒绝其遗留字节码：共 1,070 个缓存文件，其中宿主 `cpython-312` 为 637 个、`cpython-314` 为 138 个；退役 Agent 下有 56 个缓存。根因是根级 `.dockerignore` 模式未递归覆盖。现补递归规则、镜像构建检查及无源/异版/编译路径检查，并将 `.dockerignore` 纳入两仓兼容指纹和 CI 输入。保留宿主缓存，修复打包边界；33 项 Python 和 12 项 Node 定向通过。

`update.sh` 在首次应用构建失败后执行过一次自带缓存重试；没有手工反复清缓存或删除卷。已更新输入才重新执行正式构建，源输入未变的依赖和原生 SDK 层继续复用。原始输出依次保留在 `/tmp/thesis-ledger-dg2a-formal-update*.log`，失败验收报告也保留，未覆盖。

## 候选验收入口与分层

infra `bash scripts/dsa-target-acceptance.sh <正式备份目录> <新的绝对报告路径>` 持有正式维护共享锁，读取目标身份、候选源文件、依赖/原生 SDK/存储/构建上下文指纹和原挂载，再创建同一镜像的专属验证容器。验证容器无外网、根文件系统只读、不挂业务卷，仅使用 tmpfs 及只读测试资料，清理前严格核对本轮身份和资源边界。

`tests/dg2a_image_probe.py` 仅装配四个纯 Python 测试工具，生产业务代码与 Linux 原生 SDK 均来自候选镜像。复用 D0 的数据测试清单与真实数据进程，验证各公开类别、控制/OAuth、公告/目录、状态/鉴权/准入拒绝及副作用。定制 SDK 加密快照接线必须实际通过，不接受 skip。

候选镜像仅有 Python 数据运行时。Tushare 原有跨 Node Schema 断言由候选内实际 HTTP 交换生成报文，再交主仓真实 Schema 执行；原断言在宿主定向回归中仍保留，49 项通过。其余六类独立 Uvicorn 场景通过纯传输桥交主仓实际 DsaClient、DsaNavClient 及每手读取函数消费，正向和拒绝 HTTP 状态分别核对。消费的 SDK 编译产物另与目标 Server 比对，不用预制 DTO。

## 最终结果

最终正式更新命令在 infra 执行，退出 0：

```sh
rtk proxy env DEV_DATABASE_MODE=check ROLLBACK_PROTECTED_IMAGES=sha256:ffba40664b7f59baab8c575ffef7f14c3a899ed49672156e692aac7c39385477 ./scripts/update.sh dsa
```

| 候选及目标核对项 | 实际结果 |
| --- | --- |
| 最终 DSA 镜像 | `sha256:06b6054ba433de813e1bfc407872b4068ed9d9650946143c19dd5d3147c6f389` |
| 运行载荷 | 407 个文件与当前宿主源码/资产逐字节一致；汇总 SHA-256 `c22cff82827114b35c4d4aafa08c7dd58380e06c13bef7bb73cb420f1ae42e6b` |
| 兼容指纹 | 构建上下文、Dockerfile、依赖、原生 SDK 与存储所有者输入全部一致，详见原始报告 `candidate.compatibility` |
| 字节码 | 271 个当前 Python 3.11 缓存均能对应实际源文件及编译路径；宿主、无源或路径不匹配缓存为 0，退役产品文件为 0 |
| 模型边界 | `openai`、`anthropic`、`litellm`、`google.genai`、`google.generativeai`、`langchain`、`llama_index` 均物理不可见 |
| 原生 SDK | `longbridge 4.5.0+thesisledger.1`，存储接口版本 1；加密快照 → 实际 Rust/Python OAuth 绑定 → 原凭据版本保持的断言实际通过，无 skip、无授权请求 |
| 默认运行态 | Data V3、Control V3、27 项来源登记、45 个 OpenAPI 路径；健康 200、未授权 Control 401、退役分析入口 404 |
| 消费者代码 | 主仓 DSA SDK 的 26 个编译文件与目标 Server 一致；汇总 `650e8ae38340279f4ca33d4ea04b74105d4e1a17ef101276d7dc45d9dccbad49` |

实际成功执行：

```sh
rtk proxy bash scripts/dsa-target-acceptance.sh /Users/yzin/code/thesis-ledger-workspace/thesis-ledger-infra/.maintenance-backups/dg2a-20261019-1 /tmp/thesis-ledger-dg2a-target-run-7.json
```

从 `2026-10-19T16:37:45.668Z` 到 `16:39:51.617Z`（北京时间 2026-10-20 00:37～00:39），退出 0，`cleaned=true`，无 failure：

- 候选内 **210 文件、3,565 项通过，零失败、零跳过**。唯一从 pytest 本身 deselect 的 Node 用例在下一项实际执行，不计为跳过完成。
- 候选实际 Tushare HTTP/SQLite/原件/解码链生成一条事件交换，再由主仓真实 `marketEventExchangeV3Schema` 解析通过。报文 SHA-256 为 `8d34034cd8849ea7a9a4b257e173c00a22e12006ec7185d67c990396624e6cbd`；保留长小数、身份及有界覆盖语义。
- 六类独立 TCP 场景（基础/完整 K 线/事件/NAV/规则/参考资料）共 **17 项主仓实际 SDK 消费断言**：11 项正向、6 项拒绝。实际 HTTP 状态分别核对，拒绝包含 422 与 503，不用 Schema 抛错掩盖错误 HTTP 状态。六个数据进程均正常退出，产品/模型模块未导入，子进程为 0，生命周期与 OAuth 状态释放。
- 另用已维护的 `scripts/verify_data_image.sh <最终镜像>` 验证默认镜像启动、SDK 存储接口、数据路由、索引、字节码及正常停止，退出 0；没有用验收装配的启动方式替代默认入口。

受控路径覆盖由现有数据清单和断言持有：配置读取/认证/产品路由退役，报价与精确绑定，完整窗口/图表/指标与日历，事件/NAV/规则/静态参考资料，FX 冷热、反向、过期和未知时间，控制/OAuth、目录与公告任务，索引和新闻数据语义。框架层受控测试、独立 TCP 与消费侧解析分别记录；原实源事实只按未变输入复用，本轮没有发起新的实源验收。

校验实际 SDK 的同时，额外只读比对消费的共享编译产物与目标 Server：Schemas 156 文件、Domain 73 文件、Shared 1 文件全部一致，避免宿主较新解析器替代实际部署版本。

收尾再核对兼容指纹与快更事务入口：`rtk proxy node --test scripts/dsa-code-payload.test.mjs scripts/sync-code-dsa.entry.test.mjs scripts/sync-dsa-files.test.mjs` 共 26 项通过，零失败、零跳过。首次在沙箱内执行因禁止 `ps` 查询而有 3 项入口测试提前拒绝，尚未修改 Docker；允许进程查询后同一输入全部通过，未调整代码或降低门禁。入口测试使用假 Docker，文件事务使用自有临时目录。

## 最终数据保护与清理

DSA SQLite 在线只读副本的 `integrity_check=ok`；按正式更新前备份的原列逐行摘要核对，**43 张原表、225 条原记录全部一致，新增或变更差异为 0**。主仓七类保护表、结构 head、成功 V3 Run 清单和结果校验和与更新前备份一致；最初回退镜像仍可用，原数据/日志/报告挂载一致。这是本叶的保护核对，不替代 DG2b 的原件逐文件、加密引用、FX 读取及历史留存完整验收，也不替代 DG2c 的实际恢复/回退。

最终只读检查：DSA、Server、Worker 均 running/healthy，启动后日志缺表/缺列错误为 0；Server health 200，数据库/Redis/DSA 均 healthy，主库仍为 `20261019020000_research_observation_versions`。本轮 `dsa-dg2a-*` 和 `dsa-data-smoke-*` 容器均为 0，未删除业务卷或原文件。

原始成功报告 SHA-256 为 `2a13d29b471a632f8a36c60cf6e719632f9b9144f2b4b3a83157a2d3ba92f74c`；JUnit SHA-256 为 `b0efcc1e13a4e6a4e28e6f2fd01fbee2d1da9c7fc56f40f13dbd62deb89dc6d6`。失败/成功报告、矩阵日志/XML、正式更新和独立镜像日志已保存在 infra 私有忽略目录 `.maintenance-backups/dg2a-acceptance-20261020-run7/`，未将大量生成日志放入 Git。

本次运行的维护入口、合同、Python 装配及主仓消费脚本在启动前已写定，最终复核其修改时间均早于成功运行开始。关键 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| infra `scripts/dsa-target-acceptance.sh` | `956207141c743d229697651e4830149406db1038b3bc2aba0adf0d3c0bead73b` |
| infra `scripts/dsa-target-acceptance.mjs` | `dcd359604286b68a7a5ffeb55354f565a05134921fe11e270cd9d7a63490eaba` |
| infra `scripts/dsa-target-contract.mjs` | `ed314393960b22aea149c8a857f7b06e5d3b0adae832bc45ddab197154d418ad` |
| DSA `tests/dg2a_image_probe.py` | `d6bdf5ef7d1e1b03177759b3d6716d0a2231430a74def0571b97b84c531bbcd0` |
| 主仓 `scripts/dsa-target-consumer.mjs` | `83b70fc1b68f79663bd8e04dfeae622862d9c8d4d57c65b0eb837974674c1012` |

真实模型、生成任务和暂停路线维持原状态；未暂存、提交、推送或发布。CI 工作流只完成本地合同验证，不宣称 GitHub Actions 已运行。
