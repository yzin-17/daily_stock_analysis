# ThesisLedger Longbridge SDK 接入

固定上游版本为 `v4.5.0`，提交为 `68080b64ee02836e2fec625e9605dc5389abdb17`。上游来源是 [Longbridge 官方仓库](https://github.com/longbridge/openapi/tree/68080b64ee02836e2fec625e9605dc5389abdb17)。补丁及 Cargo.lock 是可复现构建输入，wheel、源码下载目录与编译缓存不进入 Git。

## 接口

扩展的 Python 包版本为 `4.5.0+thesisledger.1`。`OAuthBuilder.THESIS_LEDGER_STORAGE_VERSION == 1` 标识所需能力。

`OAuthBuilder` 新增仅关键字参数：

- `token_json`：由应用预先解密的令牌快照，使用 `client_id/access_token/refresh_token/expires_at` 字段；客户端标识必须匹配。
- `on_token_save`：接收相同格式 JSON 的保存回调；设置后完全替代默认文件存储。保存回调异常会中止授权或刷新，不回显异常中的凭证内容。
- `callback_host`：`127.0.0.1` 或 `0.0.0.0`，只改变监听地址；登记的 redirect URI 仍使用 localhost 与 callback_port。
- `allow_authorization`：普通行情运行时传 `False`，禁止在无有效凭证时启动交互授权；仅显式授权会话传 `True`。
- `authorization_timeout_secs`：1–600 秒，页面授权传 600。

SDK 补丁增加 S256 PKCE；错误 state 不消费回调，拒绝授权也校验 state。异步取消会释放回调监听，回调页只提示回到应用查看结果。加密、版本条件写入、会话状态与用户界面由 DSA 控制模块负责，SDK 只消费存储回调。

## 构建与验证

环境需要 Git、Rust 工具链和带 venv 支持的 Python。构建生成的 wheel 与调用脚本的 Python/平台一致；Linux wheel 应在目标系统兼容的构建镜像中生成，不能使用 macOS wheel。

```bash
python scripts/build_thesis_ledger_longbridge_sdk.py --output /tmp/thesis-ledger-sdk-wheels
```

脚本验证固定提交与补丁，执行 OAuth 确定性测试，再在独立 Python 构建环境生成 wheel；失败详情保留在输出中列出的构建日志。`--prepare-only` 只检查源码与补丁，不代表编译或运行时通过。

默认使用单任务编译并关闭 release LTO，以控制本机 Docker 构建峰值内存；不改变授权或令牌存储语义。可通过 `CARGO_BUILD_JOBS` 与 `CARGO_PROFILE_RELEASE_LTO` 显式覆盖。

安装所生成的 wheel 后仍需执行 DSA 接线、目标 Docker 与真实授权验收。原始 requirements 中的发行 SDK 不包含本扩展；调用前必须检查能力标识，缺失时明确报告不支持，不能退回明文文件缓存。
