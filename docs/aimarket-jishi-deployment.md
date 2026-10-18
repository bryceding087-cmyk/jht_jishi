# 建绘通 AI 集市版本部署说明

本目录 `jht_jishi` 按提供的 `url-agent` SDK、curl 示例及音频配置截图接入。

## 默认模型

| 用途 | 模型 ID | 接口 | 鉴权 |
| --- | --- | --- | --- |
| 文本 | `deepseek-v4-flash-vision-exp-0817` | `http://192.168.241.202:4091/v1/chat/completions` | `Authorization: Bearer <SDK 会话 Key>` |
| 图片 | `doubao-seedream-5.0-pro` | `http://192.168.241.202:4091/v1/images/generations` | `Authorization: Bearer <SDK 会话 Key>` |
| 视频提交 | `minimax-h3` | `http://192.168.241.202:4091/v1/video_generation` | `Authorization: Bearer <SDK 会话 Key>` |
| 视频查询 | 同上 | `http://192.168.241.202:4091/v1/query/video_generation/{task_id}` | 同一会话 Key |
| 音频 | `seed-audio-1.0` | `https://openspeech.bytedance.com/api/v3/tts/create` | `X-Api-Key: <服务端音频 Key>` |

文本使用 `model`、`messages`；图片使用 `model`、`prompt`、`n: 1`，选定的尺寸和参考图继续随请求提交。多张图片按单张请求依次生成，保留已成功结果。集市图片请求不发送 Ark 的 `watermark`、`stream` 或 `response_format` 参数。

视频默认 `resolution: "768P"`、`duration: 4`、`ratio: "16:9"`，正文为 `content: [{type: "text", text: "..."}]`；保留已有参考图支持。创建后保存 `task_id`，轮询独立查询路径，读取原协议任务状态和成片链接。当前采用轮询，无需配置 `callback_url`，不会提交示例中的占位回调地址。视频内部默认配置 ID 保留旧值以兼容已有画布引用；实际发送的模型 ID 为 `minimax-h3`。

音频超时为 300 秒。服务端只允许上述准确的 Seed Audio 接口在集市模式中使用独立音频 Key，仍校验当前集市绑定是否有效；其它来源不会得到集市 Key。音频请求拒绝重定向。

## 接入与本地配置

1. AI 集市中应用 URL 使用现有代理地址 `https://aimarket.hncig.cn:4193/market`。服务端 `JIANHUITONG_PUBLIC_ORIGIN` 默认 `https://aimarket.hncig.cn:4193`，用于代理场景的写请求来源校验；它不同于 4090 端口的集市父页面 origin。
2. `public/vendor/aimarket.js` 与提供的 `url-agent/aimarket.js` 一致。SDK 向直接父窗口发送 `aimarket:connect`，验证来源、版本及 `requestId` 后接收 `aimarket:context`。
3. 根目录 `.env` 和 `.env.example` 的服务端模型网关 `JIANHUITONG_MARKET_MODEL_ORIGIN` 已设为 `http://192.168.241.202:4091`，各接口路径包含 `/v1`。浏览器握手的父页面来源 `JIANHUITONG_MARKET_ORIGIN` 仍为 `https://aimarket.hncig.cn:4090`，它必须与用户实际打开的集市网页 origin 一致，不由服务端模型网关地址推断。如果集市网页也迁入内网，应将此变量设置为其准确 origin。`.env` 保存服务端音频密钥并被 `.gitignore` 排除；`.env.example` 不包含真实密钥。
4. 应用 ID 默认 `020884f4-99f0-4d6f-bbc4-74426e6fed1c`，通过 `JIANHUITONG_MARKET_AGENT_ID` 配置。`JIANHUITONG_MARKET_SDK_LOGIN=1` 默认启用按已确认 SDK context 的免登录。
5. 配置真实的 `JIANHUITONG_DATABASE_URL`，或运行 `scripts/setup_local_postgres.ps1` 初始化本地 PostgreSQL。当前源码目录没有历史业务数据库，未代填数据库凭据。
6. 使用 Python 3.11+ 安装 `requirements.txt`，Node.js 18+ 运行 `npm start`。启动器加载 `.env`，已有进程环境变量优先。保持单 worker 部署。

本版本信任已确认的 SDK context：按集市 origin + user.id 映射普通成员，直接进入个人首页；无需输入建绘通密码，不使用浏览器中独立管理员的身份。此模式不另行校验平台签名。集市 Key 仅保存在服务端内存；任务只存不透明引用。音频 Key 留在服务端，模型列表不返回原始 Key。

集市免登录使用页面会话 Header 和独立素材访问引用，不依赖第三方 Cookie。独立管理员登录仍沿用 Cookie；普通 HTTP 可继续使用 `lax`。详见 [接入与会话说明](aimarket-sdk.md)。

独立打开建绘通时，没有 SDK 临时 Key。可选配置独立的 `JIANHUITONG_MARKET_API_KEY` 或在模型管理中配置集市 Key；`/market` 入口不会回退使用这份共享凭据。旧的 OpenAI、Seedream、Seedance 环境密钥不会自动发送到新的集市网关。

内网部署：文本、图片和视频的模型请求已使用内网网关。音频仍按指定配置访问 `https://openspeech.bytedance.com/api/v3/tts/create`，未联网的服务器需要可达的音频代理或内网服务；仅替换集市网关不能实现音频离线调用。生成素材的下载地址也由上游响应提供，部署时需确保服务器能够访问实际返回的素材地址。

此源码包初次运行会使用以上默认模型。若后续迁入带有模型覆盖配置或默认模型指针的历史数据库，管理员需在模型管理中核对并选择上述配置；代码不会自动覆盖已有数据库中的自定义模型和密钥。

## 验证

`tests/test_aimarket_profile.py` 使用隔离数据库和模拟上游，验证默认模型、文本/图片真实请求结构、音频独立密钥及素材入库、公开配置脱敏、来源限制和过期连接处理。原有服务商测试覆盖视频创建、查询、状态解析、结果保存以及旧 Ark 协议兼容。

前端验证命令为 `node --test tests/market_session.test.mjs tests/market_entry.test.mjs`（Node.js 18+）。后端可运行 `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"`。

本次验证不使用真实模型接口。真实集市的模型授权、余额、网络连通性、多模态扩展参数和生产 iframe 部署仍以实际环境联调为准。

2026-10-18 本地验证结果：后端全套共 334 项，294 项通过，40 项因未配置 `JIANHUITONG_TEST_POSTGRES_URL` 跳过；最终默认值调整后，相关 87 项后端测试再次全部通过。前端两组集市接入测试通过，修改过的 JavaScript 语法检查通过。已确认真实音频密钥未出现在源码、前端、文档或测试中，SDK 与提供的参考文件逐字节一致。

同日将模型网关改为内网地址后，模型配置、会话隔离和服务商适配相关的 52 项测试全部通过；已确认当前项目文件（不含历史备份和依赖）没有旧公网 `/v1` 地址。未在实际内网服务器上进行连通性测试。
