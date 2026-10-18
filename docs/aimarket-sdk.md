# 建绘通 AI 集市接入与免登录

本版本按已确认的 SDK context 契约接入：集市负责获取 context，建绘通通过原版 `url-agent` SDK 收到 `user`、`agent` 和 `apiKey` 后，直接进入该集市用户的个人首页。

## 入口与流程

- 集市应用地址使用 `/market`，例如 `http://192.168.168.117:8000/market`；`/?market=1` 和 `/login?market=1` 会转到该入口。
- SDK 向直接父窗口发送 `aimarket:connect`；只接受准确来源、直接父窗口、版本和 requestId 匹配的 context。
- 集市父页面已有的 `POST /api/litellm-keys/agent/020884f4-99f0-4d6f-bbc4-74426e6fed1c/context` 继续由集市调用。建绘通不需要再复制用户的集市 Cookie 或 JWT，也不在前端保存这些凭据。
- 建绘通向自己的 `POST /api/integrations/aimarket/login` 提交 SDK context。后端按集市 origin 与 `user.id` 映射一个不可密码登录的普通成员账号，返回临时页面会话引用和独立素材访问引用。
- 页面先建立此会话，再读取 `/api/auth/me` 和业务数据。左下角显示用户姓名、部门，提示中显示工号。不会沿用另一标签页的本地管理员账号。
- 同一用户重复进入对应同一账号与历史项目；不同 ID 即使同名也不合并，项目、素材和任务沿用现有 owner 隔离。组织仅用于展示，`accountType` 不授予管理员权限。
- 独立访问 `/login` 继续使用建绘通本地账号密码；集市用户的“重新连接”会先保存，再重新获取 context，不退出整个集市。

## 接口与凭据

`GET /api/integrations/aimarket/config` 返回 `enabled`、`market_origin`、`agent_id`、`uses_session_key` 和 `sdk_login`。开启 SDK 免登录时，登录接口返回 `{connected:true,binding_id,media_token,expires_at}`，不设置第三方 Cookie。

同源业务请求（包括 `/api/auth/me` 和 `/api/auth/logout`）通过 `X-JHT-Market-Session` 携带页面引用。外部 URL、本地密码登录接口和集市配置接口不会收到该 Header。业务数据接口不允许仅凭素材引用访问。

`apiKey` 只在登录时提交到固定同源接口，服务端仅在进程内存保留，用于当前用户的模型请求，不覆盖全局模型设置。原始 Key 不写入 URL、日志、SQL 或浏览器持久化存储；前端交接后清空临时 context。模型响应与错误沿用脱敏处理。

图片、视频、音频等 `/uploads/*` 预览和 `/api/scripts/template` 下载使用单独的 `jht_media` 短期引用，只允许 GET/HEAD，仍检查文件归属。前端渲染时添加此引用，提交业务 JSON 前移除，避免把临时访问参数写入历史记录。内层同源画布继承两个随机引用，不接收模型 Key，也不依赖 Cookie。

刷新/关闭页面尽力撤销旧引用；服务端按 TTL 到期清理。`POST /api/auth/logout` 或 `DELETE /api/integrations/aimarket/session` 可撤销当前集市会话，不清除独立管理员 Cookie。关闭页面不等于取消供应商的生成任务。已提交的视频任务由后台线程持有专用的内存查询凭据，只能 GET 该供应商任务的固定查询地址；页面会话撤销后仍继续跟踪，直到完成、账号/上游授权失效或 24 小时查询窗口结束。该能力不能调用生成 POST 或其他 URL，也不写入数据库。服务进程重启后，原用户从集市重新进入可用当前 Key 继续原任务的 GET 查询。

## 配置

| 变量 | 当前默认或推荐值 | 用途 |
|---|---|---|
| `JIANHUITONG_PUBLIC_ORIGIN` | `https://aimarket.hncig.cn:4193` | 建绘通浏览器外部 origin；代理终止 HTTPS 或重写 Host 时用于写请求校验，显式空值禁用该例外 |
| `JIANHUITONG_MARKET_ORIGIN` | `https://aimarket.hncig.cn:4090` | 实际集市父网页 origin；缺失或清空则禁用集市入口 |
| `JIANHUITONG_MARKET_MODEL_ORIGIN` | `http://192.168.241.202:4091` | 服务端内网模型网关，模型路径仍含 `/v1` |
| `JIANHUITONG_MARKET_AGENT_ID` | `020884f4-99f0-4d6f-bbc4-74426e6fed1c` | SDK 应用 ID 必须一致；未设置或空值时使用此默认值 |
| `JIANHUITONG_MARKET_SDK_LOGIN` | `1` | 信任现有 SDK context 并映射个人账号；设为 `0` 可恢复先本地登录再绑定的旧模式 |
| `JIANHUITONG_MARKET_KEY_TTL` | `3600` 秒 | 限制在 60–3600 秒；到期重新连接 |
| `JIANHUITONG_COOKIE_SAMESITE` | `lax` | 仅独立账号 Cookie 流程需要；SDK 免登录流程不依赖第三方 Cookie |

按用户确认，本部署信任 SDK 传来的用户身份。服务端不另行验证平台签名或回调身份接口；来源检查和 agent.id 匹配不等于服务端签名验真。此约定是本版本身份映射的信任边界。

当前凭据在单个 Python 进程内存中，保持 `workers=1`。重启后需刷新集市应用重新握手；个人账号和业务数据仍在数据库。已有管理员历史作品不会自动迁移给新集市账号。

浏览器实际建绘通代理入口为 `https://aimarket.hncig.cn:4193/market`。新增的 `JIANHUITONG_PUBLIC_ORIGIN` 只允许准确的外部应用 origin，代理访问还须携带 `Sec-Fetch-Site: same-origin`；不会根据任意 Forwarded / X-Forwarded-* 头扩展允许来源。来源校验同样用于项目保存、素材上传和注销。

浏览器握手 origin 与模型内网网关相互独立；不要把父网页 origin 改成模型网关。普通独立页面保留防嵌入策略，只有集市入口和所需内层画布允许准确配置的集市祖先。不需要放开全站 CORS。

文本、图片、视频使用集市临时 Key。Seed Audio 保留指定的独立服务端 Key 和直连接口，详见 `aimarket-jishi-deployment.md`；本次免登录更新不修改音频协议。

视频长任务、自动入库和本次界面调整见 [视频任务与界面更新说明](video-reliability.md)。

## 验证

- `tests/test_market_sdk_login.py`：匿名业务访问拒绝、用户映射、项目与素材隔离、GET/Range 素材读取、独立管理员 Cookie、会话注销/过期、每用户模型 Key、来源和 agent 检查。
- `tests/test_market_identity.py`：稳定映射、停用账号拒绝、角色和账号命名空间保护、会话清理。
- `tests/test_market_integration.py`：保留的本地登录绑定模式与生成凭据生命周期。
- `tests/market_session.test.mjs`、`tests/market_entry.test.mjs`：SDK 消息校验、启动顺序、会话传递、媒体 URL 修饰/剥离与重连。
- `tests/market_login.browser.cjs`：真实 Chrome、跨源 HTTP iframe、无 Cookie 免登录、两个用户隔离、刷新保留身份、上传图片预览及内层画布。
- `tests/test_proxy_origin.py`、`tests/market_proxy.browser.cjs`：原来源拒绝复现、固定外部地址、拒绝其他来源，以及真实 HTTPS 代理到 HTTP 后端（包括 Host 重写）的完整浏览器流程。
- `tests/http_boot.browser.cjs`：普通 HTTP 原生 randomUUID 不可用时的兼容，以及管理员登录首页回归。

测试使用隔离数据库和虚构用户/Key，不调用真实模型。生产服务器的实际集市接入和 PostgreSQL 仍需应用更新后验收。
