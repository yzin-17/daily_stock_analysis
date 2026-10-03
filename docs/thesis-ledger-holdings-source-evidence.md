# 基金持仓来源证据

归属 M3 R04，披露时间修复对应主仓 `2026-09-27-fund-disclosure-unknown` Spec/Task。

## 未知披露时间

现有 AKShare `fund_portfolio_hold_em` 消费端按季度选择持仓，没有公告时间证据。旧 `_fund_holdings_payload` 将同一个 fetchedAt 同时写入 disclosureDate，现改为 null；保留抓取时刻、季度、未归一的权重及内容摘要，不推断季度末或公告日期。

主仓 Schema 接受 null，并将抓取时间等于披露时间的旧形态保守投影为未知；MarketService 的 24 小时缓存读取也走该 Schema，因此无需破坏性清缓存。绩效采样对未知披露时间沿用零调整、零披露/定价覆盖率及错误证据，不再读取持仓价格作估值。DSA 真实转换及 Control 回归 17 项通过，固定 fixture 的已知披露日期保留；主仓 Server 定向 6 项、Schema 全包 330 项、构建、类型及 lint、模块边界通过。

已按消费端后 DSA 顺序完成官方同步，日志 `/private/tmp/goal-holdings-consumer-sync-20260927.log`、`/private/tmp/goal-holdings-dsa-sync-20260927.log`，目标健康、镜像未变，源码在容器可写层。Server/Worker 实际包通过 Node resolve 加载，均将显式 null 和旧的同时间形态投影为 null；首次探针错误使用源码包目录，修正依赖路径后通过。估值服务编译产物宿主与两容器 SHA-256 为 `d3a9a366260c91c1c538d8800a30943889ae0c213313dcbaf9fd973ecbd20b31`；DSA API 宿主与目标为 `dc48b3e10d99bcd78e4edfbc2805360bcb294c51accd4e55140cf140aad29380`。

目标 HTTP `/private/tmp/goal-holdings-api-probe-20260927.py` 首次 200，000001.OF 由 AKShare 返回 2026-Q2 共 77 条持仓，disclosureDate=null，fetchedAt=`2026-09-27T04:16:30.956221+00:00`。完整响应在进程内传给实际 Server Schema 验证成功，未将原始响应或凭据打印。未修改策略、准入或删除缓存；正常 HTTP 可产生诊断与请求计数。此证据只覆盖未知时间表达和消费，77 条不证明披露完整性或真实公告时间。

披露时间缺失仍是来源门禁，null 表达不等于完成历史可见性验证。专题无对应英文版。

## 报告期与行完整性

持仓原始行现在要求可唯一识别的四位年份及季度、非空字符串身份、有限的 0–100 百分比，以及每季度合计不超过 100.0001。同报告期重复原始代码拒绝整批，跨报告期同代码允许；API 对最新报告期的规范化重复代码返回错误，不再累加，也不再静默跳过未知报告期。

本地持仓行 19 项、Provider runtime 27 项、Data gateway 7 项通过，披露时间与 Control 17 项回归通过；相关文件的 flake8 E9/F63/F7/F82 检查通过。上述为本地证据，目标运行态另行核验。对应主仓 `2026-09-27-fund-holdings-row-validation` Spec/Task；不证明来源完整覆盖或真实披露时间。

官方 `sync-code.sh dsa` 成功，日志 `/private/tmp/goal-holdings-row-sync-20260927.log`，目标 healthy。宿主与目标 SHA-256 一致：持仓 helper `fef63604e293a28a16a409175560ff616c532594611b8b48fb496cc0a0958886`；Provider runtime `887df8b6c8e42cd082f26e20952e3cd0b110a77a345e83ae87bc4892cc51a9e4`；API `8812ba3fda0f31ea022aa8787bbef16ab2b61ed95d85a72eba010f4e36cf2d58`。源码同步只更新容器可写层。

实际 HTTP 首次 200，000001.OF、AKShare、2026-Q2、77 条、disclosureDate=null，fetchedAt=2026-09-27T04:26:39.224731+00:00；完整响应通过目标 Server 实际 Schema。探针 `/private/tmp/goal-holdings-api-probe-20260927.py`，无需重试；未修改策略/准入或创建回测与 AI 任务。此后续证据替代上节旧 API 文件摘要，不改变历史请求结果。
