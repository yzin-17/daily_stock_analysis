# HiThink 页面凭证与连接测试

## 配置入口

ThesisLedger 的“市场数据 → 数据源 → HiThink → 配置凭证”使用必填的保密 `API Key` 字段，认证方式为 `api_key`。Control V3 的 Provider manifest 通过 `credentialSchema.methods` 声明表单；主仓按该声明复用凭证侧栏。

凭证通过 Control 的 Provider config 接口交给 DSA，加密保存在现有 SQLite。接口只返回字段是否已配置、配置版本和来源，不回显 API Key。页面优先于 `HITHINK_API_KEY` 环境变量；页面字段留空只保留已有页面值。移除页面凭证后才恢复环境配置。页面凭证无法解密或格式失效时不静默回退环境账号。

## 运行时与独立准入

ETF/股票日线、报价和 ETF 分红均消费同一个有效凭证快照。内部 HMAC 将页面来源与环境来源区分：相同 Key 从环境转为页面也不会继承原账号来源的独立准入，轮换后须使用当前凭证重新验证需准入的能力。

HiThink ETF 前复权基础价格按已有共同能力契约检查配置即可；股票日线、报价和事件仍保留各自精确准入及读前读后核验。页面保存成功并不授予其他能力。能力清单与基础价格边界见[来源能力](thesis-ledger-source-capabilities.md)。

## 测试连接

草稿测试使用本次输入，不落库、不修改业务路由或准入，也不写入已保存凭证的健康状态。HiThink 按能力调用现有只读读取器：股票单标快照、单个已知交易日的股票前复权日线，以及 ETF 现金分红接口。每项最多一次来源调用、超时至多 4.5 秒；接口错误映射为稳定脱敏错误码。

单个代表请求仅证明该来源接口当次可达与权限，不证明 ETF 全部接口、所有标的、长期覆盖或历史可见性。分红返回不完整历史仍保持原语义，不因连接通过将事件标为完整。

## 验证入口

隔离 HTTP/SQLite 测试：`tests/test_hithink_control_credentials.py`。它覆盖字段声明、加密重开、保留、轮换、环境恢复、失效拒绝和草稿调用；来源边界使用合成结果，不宣称真实 API Key 权限。

生产读取回归分别见 `tests/test_thesis_ledger_hithink_credential_admission_runtime.py`、`tests/test_thesis_ledger_hithink_stock_runtime_v3.py`、`tests/test_thesis_ledger_hithink_quote_runtime.py` 与 `tests/test_thesis_ledger_hithink_events_v3.py`。主仓负责前端默认主源与目标验收，见[对应规格](../../thesis-ledger/docs/specs/2026-10-07-market-hithink-defaults-credentials.md)。
