# ThesisLedger 规则接口错误

公共费用 `/api/v3/thesis-ledger/market/public-fees` 与旧交易状态 `/api/v3/thesis-ledger/market/trading-status` 沿用原成功响应。失败响应统一采用 Data V3 信封：`detail` 内包含 `contractVersion: 3`、`requestId` 和 `error: { code, message }`。消息由服务端安全映射生成，不返回 Provider 异常原文。

来源不可用、未选中或尚未准入返回 `upstream_failure`／503；覆盖不足返回 `insufficient_coverage`／422；参数错误返回 `invalid_request`／422；来源数据无效返回 `invalid_response`／502。权限、来源认证、限流及额度不足保留各自稳定错误码。Data Token 认证仍由原认证入口处理。

独立状态 `/api/v3/thesis-ledger/market/status-capabilities` 继续使用相同的标准错误信封。来源报告数据无效只说明该次来源不能提供可信事实，不能据此补造正常交易状态。ThesisLedger 消费端在严格核对请求关联后，可按已保存的人工主备尝试备用；可选能力全部失败时保留失败事实与警告，必需能力仍按其完整性要求阻断。畸形信封、请求身份错配及成功响应损坏继续作为协议错误拒绝。

跨仓验收契约见 [ThesisLedger 规格](../../../thesis-ledger/docs/specs/2026-11-12-status-source-failure-isolation.md)。原始坏响应字节未保存时，只能确认已观察到的错误链路及当前复现结果，不能把一次来源恢复解释成持续可用。
