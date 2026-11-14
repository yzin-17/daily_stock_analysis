# 数据源页面凭证与本机授权

ThesisLedger 的“市场数据 → 数据源 → 配置凭证”支持页面填写凭证。启用开关即时保存；凭证字段使用独立的“保存凭证”。页面凭证优先于 DSA 环境配置，移除页面凭证后恢复环境配置。

页面凭证只用于 ThesisLedger 的请求与连接测试，不改变独立 DSA 分析配置。手动凭证和 Longbridge OAuth 令牌加密保存在 DSA 的 SQLite 数据库，依赖 `/app/data` 持久卷及稳定的 `THESIS_LEDGER_DSA_SECRET_KEY`。更换密钥需要按密钥轮换流程操作。

## HiThink 本机凭据

HiThink 支持页面 API Key，并以 `HITHINK_API_KEY` 作为环境回退。本机 Compose 将该变量传入 DSA 容器；可在运行官方更新入口的进程环境中提供，也可放入未提交的本机 `.env`。`~/.zshrc` 中的变量不会自动进入已有容器，更新后应通过数据源目录核对 `credentialConfigured`，无需输出密钥值。

基础 ETF qfq 配置有效 API Key、启用来源并保存精确路由即可尝试读取，不再要求人工窗口准入或额外的准入审计签名密钥。页面凭据加密存储仍需要稳定的 `THESIS_LEDGER_DSA_SECRET_KEY`。腾讯 ETF none/qfq/hfq 无需 API Key；实际接口响应、覆盖和高级能力分别检查。当前共同价格基线见主仓 Spec §1.1，真实回测验收与镜像发布分开记录。

## Longbridge 浏览器授权

本机 Docker 使用 `compose.yml` 与 `compose.dev.yml`。后者将回调端口发布到 `127.0.0.1:60355`，同时让 DSA 在容器内监听 `0.0.0.0`。Longbridge 应用回调地址为 `http://localhost:60355/callback`。浏览器和 Docker 必须运行在同一台电脑；远程服务器授权不在本次范围内。

在配置侧栏选择“浏览器授权”，填写应用 Client ID 后点击“授权并保存”，在系统浏览器完成授权。侧栏可重新打开查看状态；取消、超时和进程重启中断的会话不会覆盖原凭证。普通行情请求只能加载已有令牌，不能触发浏览器授权。三项密钥方式仍可使用。

DSA 镜像包含固定源码构建的 `longbridge 4.5.0+thesisledger.1`，提供加密存储回调和禁止隐式授权能力。只有上游标准 SDK 的运行环境会拒绝页面 OAuth；本机直接运行 DSA 时应使用仓库的 `scripts/build_thesis_ledger_longbridge_sdk.py` 构建并安装对应 wheel。

## 验证

运行 `bash scripts/provider-oauth-contract.test.sh` 检查本机端口、监听配置与持久卷。真实授权需要可用的 Longbridge 应用和账号；页面“保存成功”不代表行情权限或在线连接已通过。
