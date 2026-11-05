# MiniRacer原生运行时串行化规格

关联[主仓行情交付](../../../thesis-ledger/docs/archive/tasks/2026-11-04-market-chart-followups.md)及[本仓任务](../archive/tasks/2026-11-05-miniracer-runtime-serialization.md)。

## 观察与根因依据

2026-11-04纯代码同步后，ARM64目标DSA于15:56:39 UTC以133退出，OOMKilled=false。原生栈来自py_mini_racer/libmini_racer.so，包含partition_address_space.cc的IsConfigurablePoolInitialized检查。健康检查曾通过，随后进程退出，不能把启动健康当作持续业务可用。

[上游问题100](https://github.com/bpcreech/PyMiniRacer/issues/100)给出了相同原生检查、MiniRacer 0.14.1与Akshare线程池场景的复现，指出全局初始化锁没有覆盖并发isolate创建。当前宿主依赖为mini-racer 0.14.1，公开MiniRacer构造、eval／execute／call／close接口已核对；目标具体版本及守卫安装后效果须在恢复时另验。依据匹配栈，当前修复针对并发同步JS实例操作，不把本地测试推定为目标运行时证明。

## 修复边界

在数据服务入口导入来源模块之前安装进程级可重入锁。对公开同步MiniRacer构造、eval、execute、call及关闭／上下文退出方法加同一锁，覆盖实际Akshare同步JS路径。保持原类身份、参数、返回值和异常；嵌套execute／eval可重入，重复安装不叠加包装。未安装可选JS依赖时保持可选状态，其他导入错误不吞掉。锁只限制本进程JS原生操作并发，不串行化所有来源网络请求。

不通过捕获Python异常处理原生SIGTRAP；原生崩溃无法由普通try/except恢复。启动安装、受控并发与Linux ARM64真实进程分别验收。当前任务不改Provider参数、来源路由或业务数据库；临时行情路由仍由主仓既有授权及预算持有，真实来源读取尚未开始。

## 验证与恢复

先以受控类验证最大并发1、重入、异常、身份与幂等，再在独立宿主进程运行实际JS并发。目标容器已退出，按工作区规则使用infra/scripts/update.sh dsa恢复；不绕过入口单独启动或重启。恢复后在目标只读独立进程核对包版本与实际并发JS，随后验证Control目录与Provider状态持续可用，最后返回主仓原任务。所有原始失败和正式入口结果保留，不用本修复关闭行情实源或其他交付叶。
