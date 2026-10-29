# URL Agent 接入 AI 集市与服务器部署指南

更新日期：2026-10-25。以建绘通 `jht_jishi` 当前代码为例，说明其他 Web 项目如何接入 AI 集市，并通过 aTrust / JumpServer 部署到 Linux 服务器。

目标是完成这条链路：**用户从集市打开应用 → URL Agent SDK 获取当前用户和临时 Key → 应用建立自己的会话 → 后端调用模型并保存业务数据 → 用户在 HTTPS 页面中查看结果。**

本文包含接入协议、前后端职责、配置模板、离线发布和验收步骤。域名、内网 IP、应用 ID、容器名均采用本项目示例；迁移到其他项目时按第 11 节替换。示例发布版本 `20261025-1` 不表示服务器已经运行该版本；本文依据本地代码整理，未重新连接生产服务器验证。

## 1. 明确 SDK、应用和服务器各自负责什么

| 部分 | 本项目实现 | 负责的事情 |
| --- | --- | --- |
| AI 集市父页面 | 平台提供 | 打开应用 iframe，响应握手，提供 `user`、`agent`、`apiKey` |
| URL Agent SDK | `public/vendor/aimarket.js` | 向直接父窗口握手，校验消息来源、协议版本及请求编号 |
| 应用前端适配 | `public/market-session.js` | 先连接集市，再创建应用会话，携带业务与媒体访问凭据 |
| 应用后端接入 | `market_service.py`、`market_identity.py` | 用户映射、会话管理、凭据绑定和模型调用范围限制 |
| 业务服务 | `app.py` 及生成服务 | 项目、对话、任务、素材、生成和结果保存 |
| 数据存储 | PostgreSQL、媒体数据卷 | 保存账号和业务记录、上传与生成文件 |
| 服务器与反向代理 | Docker、HTTPS 代理 | 运行服务、持久化数据、提供浏览器可访问的 HTTPS 地址 |
| aTrust / JumpServer | 单位提供 | 运维登录和文件上传 |

URL Agent 在这里是一种 **iframe + postMessage 的浏览器接入方式**。SDK 不会自动创建本地账号、实现业务鉴权、转发模型请求或部署服务器。这些能力需要应用自己的代码完成。

项目的运行链路如下：

```mermaid
sequenceDiagram
    participant M as AI 集市父页面
    participant F as 应用 iframe / SDK
    participant B as 应用后端
    participant D as PostgreSQL / 媒体存储
    participant G as 模型网关
    M->>F: 打开 HTTPS /market
    F->>B: GET 接入配置
    B-->>F: market_origin、agent_id、sdk_login
    F->>M: aimarket:connect，version=1，requestId
    M-->>F: aimarket:context，user、agent、apiKey
    F->>B: POST /api/integrations/aimarket/login
    B->>D: 映射用户、创建应用会话记录
    B-->>F: binding_id、media_token、expires_at
    F->>B: 带应用会话请求业务接口
    B->>G: 使用当前用户临时 Key 调用模型
    G-->>B: 结果或异步任务编号
    B->>D: 保存归属明确的任务和媒体
    B-->>F: 返回业务结果与媒体路径
```

**本项目的身份信任约定：**后端按现有平台契约接受 SDK context，没有另外校验平台签名或向平台核验用户身份。SDK 的 origin 校验及 Key 的 `sk-` 格式检查不等同于后端身份验真。其他项目接入前应确认平台实际的身份契约；若平台提供签名票据、授权码或身份查询接口，应在创建本地会话前完成对应核验。

## 2. 向平台取得接入参数，登记自己的应用

### 2.1 先分清三个地址和一个应用 ID

| 参数 | 本项目示例 | 用途 |
| --- | --- | --- |
| 集市父页面 origin | `https://aimarket.hncig.cn:4090` | SDK `marketOrigin`、父页面消息校验和 iframe 允许来源 |
| 应用公开 origin | `https://aimarket.hncig.cn:4193` | 浏览器访问自己的应用，以及后端写请求来源校验 |
| 模型网关 origin | `http://192.168.241.202:4091` | 部署服务器向模型服务发请求 |
| 集市应用 ID | `020884f4-99f0-4d6f-bbc4-74426e6fed1c` | 将 SDK 下发的 `agent.id` 与当前应用对应 |

Origin 仅包含协议、主机和端口，不带路径、查询参数或末尾斜杠。例如 `https://aimarket.hncig.cn:4090/market` 不能作为 `marketOrigin`。模型 ID 是另一项参数，不能把 `agent.id` 当作模型名称。

本项目提供给平台的完整应用入口是：

```text
https://aimarket.hncig.cn:4193/market
```

### 2.2 接入前需要的平台信息

由项目负责人与平台管理员对齐以下内容，记录到项目自己的配置表：

1. 为新项目登记应用，取得它自己的应用 ID，登记最终 HTTPS 入口。
2. 确认用户实际访问的集市 origin，以及平台使用的 URL Agent SDK / 协议版本。
3. 确认 context 的用户字段、身份信任方式，以及是否允许应用后端临时使用该 Key。
4. 确认可调用模型、接口路径、鉴权头、额度和 Key 失效规则；异步任务还要确认后续查询权限。
5. 确认浏览器到应用入口、应用服务器到模型网关及媒体下载地址的网络路径。
6. 根据应用功能配置 iframe 权限。启用 sandbox 时核对脚本和正常 origin 所需权限；下载、全屏、剪贴板等功能按需单独配置。

本项目记录的集市父页会调用 `POST /api/litellm-keys/agent/<应用ID>/context` 获取上下文。该请求由平台负责；应用只接收 SDK 交付的 context，不需要复制集市的 Cookie / JWT，也不应未经平台约定自行调用这一路径。

## 3. 前端接入：先握手，再加载业务

### 3.1 提供可匿名加载的接入页面

本项目由后端提供 `/market`，返回应用页面，并允许加载必要的 JS、CSS 和图片。访问该页面不需要先输入本地账号密码；项目、任务等业务 API 仍需有效应用会话。

其他项目可以保留自己的普通登录入口，另外增加 `/market` 或等价入口。在前端启动时识别当前模式：

```text
集市入口：加载配置 → SDK 握手 → 创建应用会话 → 获取当前用户 → 加载项目与业务数据
普通入口：执行项目原有的登录与启动流程
```

不要让普通登录检查先把集市用户跳到 `/login`。本项目在 `public/app.js` 的 `boot()` 中先等待 `initializeMarket()`，成功后才调用用户和业务接口。

### 3.2 使用真实 SDK 接口

项目中的 SDK 是无依赖的浏览器脚本，加载后导出 `globalThis.aimarket.connect`：

```javascript
import '/vendor/aimarket.js';

const controller = new AbortController();
const context = await globalThis.aimarket.connect({
  marketOrigin: 'https://aimarket.hncig.cn:4090',
  timeoutMs: 15000,
  signal: controller.signal,
});
// context 包含 user、agent、apiKey；交给应用会话流程，不输出到控制台。
```

SDK 默认等待 15 秒，每 500 毫秒重发同一个握手，使用同一个 `requestId`。它只重试握手，不负责重试模型生成。浏览器顶层直接打开 `/market` 时没有集市父窗口，会得到 `NOT_EMBEDDED`；完整登录必须从真实集市或受控测试父页进入。

子页面发送的消息结构：

```json
{
  "type": "aimarket:connect",
  "version": 1,
  "requestId": "本次握手随机编号"
}
```

父页面返回的 context 示例，所有身份和 Key 均为演示值：

```json
{
  "type": "aimarket:context",
  "version": 1,
  "requestId": "与本次握手相同的随机编号",
  "user": {
    "id": "platform-user-001",
    "name": "测试用户",
    "accountType": "ordinary",
    "workNumber": "TEST001",
    "organization": {
      "id": "department-001",
      "name": "测试部门",
      "type": "department"
    }
  },
  "agent": {
    "id": "REPLACE_WITH_OWN_AGENT_ID",
    "name": "新项目名称"
  },
  "apiKey": "sk-example-not-a-real-key"
}
```

SDK 同时核对 `event.origin`、`event.source === window.parent`、`version === 1` 和 `requestId`。业务适配层及后端还要校验 `agent.id` 是自己的应用 ID。

| 字段 | 当前契约 | 应用侧使用方式 |
| --- | --- | --- |
| `user.id` | 字符串 | 外部用户稳定标识，用于账号映射 |
| `user.name` | 字符串 | 展示名称，不能作为唯一身份 |
| `user.accountType` | `group` / `ordinary` | 平台账号类型，不能直接提升为本地管理员 |
| `user.workNumber` | 字符串或 `null` | 展示或业务资料 |
| `user.organization` | 对象或 `null` | 主组织资料，不代表全部资源授权 |
| `organization.type` | `organization` / `department` / `virtual` | 组织类型 |
| `agent.id`、`agent.name` | 字符串 | 应用身份与显示名称 |
| `apiKey` | `sk-` 开头的字符串 | 当前模型调用凭据，格式通过不代表已验证有效性 |

`workNumber`、`organization` 无值时使用 `null`。SDK 返回 `{user, agent, apiKey}`，应用提交后端时只传这三个字段；不要把 postMessage 的 `type`、`version`、`requestId` 一起提交到本项目登录接口。

### 3.3 把 context 换成应用自己的会话

以下是面向 SDK 免登录模式的首次连接骨架，要求后端已经实现第 4 节的接口。它保留两个应用凭据在模块内存中，完成登录后清空 context 中的原始 Key：

```javascript
import '/vendor/aimarket.js';

let bindingId = '';
let mediaToken = '';
const controller = new AbortController();

export async function connectAndLogin() {
  let context = null;
  try {
    const configResponse = await fetch('/api/integrations/aimarket/config', {
      credentials: 'omit', cache: 'no-store', signal: controller.signal,
    });
    if (!configResponse.ok) throw new Error('无法读取集市配置');
    const config = await configResponse.json();
    if (!config.enabled || config.sdk_login !== true || config.uses_session_key !== true) {
      throw new Error('集市免登录未启用');
    }

    context = await globalThis.aimarket.connect({
      marketOrigin: config.market_origin, signal: controller.signal,
    });
    if (context.agent.id !== config.agent_id) throw new Error('集市应用 ID 不匹配');

    const { user, agent } = context;
    const response = await fetch('/api/integrations/aimarket/login', {
      method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user: {
          id: user.id, name: user.name, accountType: user.accountType,
          workNumber: user.workNumber,
          organization: user.organization === null ? null : {
            id: user.organization.id, name: user.organization.name,
            type: user.organization.type,
          },
        },
        agent: { id: agent.id, name: agent.name },
        apiKey: context.apiKey,
      }),
    });
    if (!response.ok) throw new Error('应用会话创建失败');
    const result = await response.json();
    const validToken = value => typeof value === 'string' && /^[A-Za-z0-9_-]{16,200}$/.test(value);
    if (result.connected !== true || !validToken(result.binding_id) || !validToken(result.media_token)) {
      throw new Error('应用会话响应无效');
    }
    bindingId = result.binding_id;
    mediaToken = result.media_token;
  } finally {
    if (context) context.apiKey = '';
    context = null;
  }
}

export async function loadCurrentUser() {
  if (!bindingId) throw new Error('请先连接集市');
  const response = await fetch('/api/auth/me', {
    credentials: 'omit', cache: 'no-store', redirect: 'error',
    headers: { 'X-JHT-Market-Session': bindingId },
  });
  if (response.status === 401 || response.status === 428) {
    bindingId = mediaToken = '';
    throw new Error('会话已失效，请重新连接集市');
  }
  if (!response.ok) throw new Error('读取当前用户失败');
  return response.json();
}

window.addEventListener('pagehide', () => {
  controller.abort();
  const previousBinding = bindingId;
  bindingId = mediaToken = '';
  if (previousBinding) {
    void fetch('/api/integrations/aimarket/session', {
      method: 'DELETE', credentials: 'omit', keepalive: true, redirect: 'error',
      headers: { 'X-JHT-Market-Session': previousBinding },
    }).catch(() => {});
  }
});

// 由应用启动函数依次调用：
// await connectAndLogin();
// const { user } = await loadCurrentUser();
// 然后加载业务数据。
```

上例使用 `credentials: 'omit'` 演示不依赖 Cookie 的集市模式。本项目完整适配器兼容普通登录，使用 `same-origin`，但后端以有效集市会话优先识别当前用户；不会把另一个标签页的管理员 Cookie 当作集市用户身份。

完整页面还需处理连接中、失败、重新连接、重复初始化、退出与嵌套页面；本项目实现可参考 `public/market-session.js`。重连时先清理旧会话及状态，等待新绑定建立，再恢复业务读取，避免自动重发结果未知的模型生成请求。

### 3.4 业务接口和媒体请求分开携带凭据

| 请求 | 凭据方式 | 范围 |
| --- | --- | --- |
| 同源业务 API | `X-JHT-Market-Session: <binding_id>` | 当前应用的业务接口，包括 `/api/auth/me`、`/api/auth/logout` |
| 本地密码登录、公开配置 | 不自动附加集市绑定 | 保持各自协议 |
| 会话撤销 | 明确附加绑定到 `DELETE /api/integrations/aimarket/session` | 撤销本页面会话 |
| 图片 / 视频 / 音频标签 | `/uploads/文件?jht_media=<media_token>` | 后端只允许规定路径的 GET / HEAD，并校验文件归属 |
| 外部模型或其他站点 | 不附加应用会话与素材凭据 | 模型请求由后端使用对应的模型 Key |

`img`、`video`、`audio` 的普通 `src` 请求不会自动附加 fetch 的自定义 Header，因此需要单独设计媒体访问。本项目的 `jht_media` 还允许读取 `/api/scripts/template`，不能凭它读写其他业务接口。代理需要保留视频的 Range 请求和响应。

素材 token 只在展示时加到 URL，提交保存项目、对话或任务正文前应移除。本项目的 `marketMediaUrl()`、`marketResponseData()`、`withMarketSession()` 实现了这层转换。原始 `apiKey` 不能出现在媒体 URL 中，代理访问日志也应避免记录含 `jht_media` 的完整查询串。

如果应用内部还有同源 iframe，本项目采用独立的 `jht-market-session:request/response` 消息让内部画布继承应用会话，校验同源、父窗口和 iframe 实例。内部页面不向更外层集市重复握手，也不接收原始模型 Key。

## 4. 后端需要实现的接口和状态

以下路由和 `X-JHT-*` 命名是建绘通自己的设计，**不是 URL Agent SDK 自动提供的服务**。其他项目可以更名，但前后端必须保持一致。

| 接口 | 本项目行为 | 典型响应 |
| --- | --- | --- |
| `GET /market` | 返回可加载的接入页面；无集市 origin 时为 404 | HTML |
| `GET /api/integrations/aimarket/config` | 公开必要接入配置，不返回 Key | 200 |
| `POST /api/integrations/aimarket/login` | 校验 context，映射集市用户，建立应用会话 | 201 |
| `GET /api/auth/me` | 根据应用会话返回当前用户 | 200；未登录 401，绑定失效可为 428 |
| `DELETE /api/integrations/aimarket/session` | 撤销绑定、媒体访问及关联集市应用会话 | 204 |
| `POST /api/auth/logout` | 集市模式撤销当前绑定；普通模式处理本地登录 | 204 |
| `POST /api/integrations/aimarket/session` | 旧模式：先本地登录再绑定 Key | 201；启用 SDK 免登录时使用 `/login` 接口 |

公开配置响应示例：

```json
{
  "enabled": true,
  "market_origin": "https://aimarket.hncig.cn:4090",
  "agent_id": "REPLACE_WITH_OWN_AGENT_ID",
  "uses_session_key": true,
  "sdk_login": true
}
```

登录成功响应示例，token 和时间均为演示值：

```json
{
  "connected": true,
  "binding_id": "EXAMPLE_RANDOM_PAGE_SESSION_REFERENCE",
  "media_token": "EXAMPLE_RANDOM_READ_ONLY_MEDIA_REFERENCE",
  "expires_at": 1792886400
}
```

`expires_at` 为 Unix 秒时间戳。当前应用 TTL 默认 3600 秒，可配置范围 60–3600 秒；这是应用绑定有效期，不保证上游 Key 在同一时间内始终有效。

后端处理顺序应当是：

1. 检查应用公开 origin 和请求来源；执行与平台约定的身份核验或信任流程。
2. 校验字段类型、长度和 `agent.id`，拒绝顶层额外字段，限制登录尝试频率。
3. 使用“平台 issuer + `user.id`”映射本地账号。本项目 issuer 为配置的集市 origin，账号为普通成员且禁止本地密码登录；同名用户不合并，`accountType=group` 也不授予管理员权限。
4. 为本次连接生成独立的随机 `binding_id` 和 `media_token`，绑定本地账号、有效期及当前 Key。
5. 每次业务与文件访问检查绑定、用户状态及资源归属；模型任务记录保存账号和不透明凭据引用。
6. 在过期、退出或停用后拒绝继续使用绑定，模型 Key 不回退为其他用户或共享管理员的 Key。

本项目将原始模型 Key 和绑定保存在 Python 进程内存，数据库保存本地用户、应用会话哈希和业务记录。前端交接后不在 localStorage、sessionStorage、数据库或日志里持久化原始 Key。应用存储的独立模型配置与临时集市 Key 是不同来源。

由于绑定、调度和部分任务凭据在进程内存中，部署必须保持**单个应用实例、单个 Uvicorn worker**。不能仅靠增加 worker 扩容。重启后原用户重新进入集市恢复凭据；只有已知供应商任务号且新凭据有查询权限时，才能继续查询原任务。改变集市 origin 会影响本项目的账号映射，应提前安排映射迁移。

## 5. 后端调用 AI 集市模型

当前项目的服务端默认接口如下，模型 ID 以 2026-10-25 工作区为准：

| 用途 | 模型 ID | 网关路径 | 鉴权 |
| --- | --- | --- | --- |
| 文本 | `deepseek-v4.1-flash` | `/v1/chat/completions` | `Authorization: Bearer <当前用户SDK Key>` |
| 图片 | `doubao-seedream-5.0-pro` | `/v1/images/generations` | 同上 |
| 视频提交 | `minimax-h3` | `/v1/video_generation` | 同上 |
| 视频查询 | 同一视频模型 | `/v1/query/video_generation/{task_id}` | 对应任务有权限的会话 Key |
| 音频 | `seed-audio-1.0` | `https://openspeech.bytedance.com/api/v3/tts/create` | 独立的服务端 `X-Api-Key` |

前四项在服务器上使用 `http://192.168.241.202:4091` 作为 origin；本地开发默认使用 `https://aimarket.hncig.cn:4090`。音频是本项目的独立接口例外，不代表集市 Key 自动具有音频授权。

文本请求体示例：

```json
{
  "model": "deepseek-v4.1-flash",
  "messages": [{"role": "user", "content": "请回复接入成功"}]
}
```

后端在执行请求时从当前用户绑定解析 Key。项目内可以参考 `model_for_generation()`、`model_api_key()`、`worker_scope()` 及 `stamp_market_credentials()`；不要把临时 Key 写入全局模型配置或排队任务 JSON。

项目的模型请求还满足以下约束：

- 模型网关由服务端配置，不从 SDK context 接受任意模型 URL；当前集市 Key 只用于配置允许的模型 origin。
- 文本、图片、视频分别适配实际请求与响应协议，不能仅更换模型名称。图片参数、参考素材、视频时长及分辨率需遵循对应模型能力。
- 视频提交后保存 `task_id` 并查询状态；当前未使用回调地址。请求超时、关闭页面或本地停止，都不能据此认定供应商任务已撤销或未计费。
- 服务端需访问实际返回的媒体下载地址，生成成功不代表媒体已经保存到应用数据卷。
- 供应商错误和结果在返回页面、写日志或存库前处理密钥脱敏。

本项目内置网关地址切换由 `model_gateway.py` 处理；已有数据库中的自定义模型不会全部被新环境覆盖，部署后仍需核对模型配置。当前图片任务不限制应用层并发，视频每个账号上限为 10、全系统合计上限为 100，连续 30 分钟无有效进展会超时；共享凭据池不再额外限流，旧的图片 / 视频供应商并发环境变量已不再使用。其他项目按自己的额度和调度实现设置限制。

## 6. 准备服务器运行配置

### 6.1 本项目运行基线

| 项目 | 配置 |
| --- | --- |
| 已有目标环境记录 | 麒麟 V10、x86_64；专用引擎安装脚本要求内核不低于 4.18 |
| 应用运行时 | Python 3.11、FastAPI、Uvicorn，依赖见 `requirements.txt` |
| 前端 | `public/` 静态文件，由同一后端提供，无需单独前端构建 |
| Docker 入口 | `python /app/deploy/container_entrypoint.py`，等待数据库后调用 `server.py` |
| Node.js | 用于本地 `npm start` 启动器；应用 Docker 镜像不需要 Node.js |
| 应用容器 | `jht-app`，UID/GID `10001:10001`，端口 `8000` |
| 数据库容器 | `jht-postgres`，镜像 `postgres:17-bookworm`，本机 `127.0.0.1:55432` |
| 数据库账号 / 库 | `jianhuitong_app` / `jianhuitong` |
| 数据持久化 | `jht-postgres-data` 保存数据库，`jht-app-data` 挂到 `/app/data` |
| 媒体 | `/app/data/uploads/`，运行时需要 FFmpeg、libass 和中文字体 |
| 网络 | 两个容器采用宿主网络，不使用 `-p` 端口映射 |

通过 JumpServer 在目标资产检查：

```bash
cat /etc/os-release
uname -m
uname -r
df -h
free -h
ss -lnt
command -v systemctl tar sha256sum curl gzip
command -v docker podman jht-docker
```

确认目标架构、安装权限、磁盘空间、端口占用及内外网可达性。宿主网络模式下，同机其他项目必须使用不同端口；数据库不向外开放。服务器能连接模型网关，也仍需检查音频服务及真实媒体下载地址。

### 6.2 应用配置模板

正式部署将以下内容保存为发布目录的 `config/app.env`。所有 `REPLACE_WITH_...` 都需要替换；其他项目还要替换项目专属变量及参数。

```dotenv
HOST=0.0.0.0
PORT=8000
JIANHUITONG_DATA_DIR=/app/data
JIANHUITONG_DATABASE_URL=postgresql+psycopg://jianhuitong_app:REPLACE_WITH_URL_ENCODED_APP_DB_PASSWORD@127.0.0.1:55432/jianhuitong

JIANHUITONG_PUBLIC_ORIGIN=https://aimarket.hncig.cn:4193
JIANHUITONG_MARKET_ORIGIN=https://aimarket.hncig.cn:4090
JIANHUITONG_MARKET_MODEL_ORIGIN=http://192.168.241.202:4091
JIANHUITONG_MARKET_AGENT_ID=REPLACE_WITH_OWN_AGENT_ID
JIANHUITONG_MARKET_SDK_LOGIN=1
JIANHUITONG_MARKET_KEY_TTL=3600

JIANHUITONG_MARKET_API_KEY=
SEEDAUDIO_API_KEY=
JIANHUITONG_COOKIE_SAMESITE=lax
FORWARDED_ALLOW_IPS=127.0.0.1
```

配置说明：

- `JIANHUITONG_PUBLIC_ORIGIN` 使用浏览器实际访问的应用 origin；`MARKET_ORIGIN` 使用集市父页面 origin，两个值不要混填。
- `MARKET_AGENT_ID` 填新项目自己的 ID。部署建绘通时使用第 2 节给出的建绘通 ID。
- `MARKET_API_KEY` 只在需要独立入口模型凭据时使用，可以留空；集市 `/market` 不回退使用该共享 Key。
- `SEEDAUDIO_API_KEY` 是本项目音频服务凭据，不使用音频时可以留空。
- `FORWARDED_ALLOW_IPS=127.0.0.1` 适用于同机反向代理；远端代理改为实际代理出口 IP，并限制后端端口的访问来源。
- 若仅由同机代理访问应用，可将 `HOST` 改为 `127.0.0.1`。
- 正式容器配置与开发机 `.env` 分开。本项目显式进程环境优先于 `.env`；Docker 默认网关为内网地址，本地启动默认网关为集市公网地址。

数据库首次初始化配置放到 `config/postgres.env`：

```dotenv
POSTGRES_USER=postgres
POSTGRES_DB=postgres
POSTGRES_PASSWORD=REPLACE_WITH_DATABASE_ADMIN_PASSWORD
JHT_APP_PASSWORD=REPLACE_WITH_APP_DB_PASSWORD
```

`JHT_APP_PASSWORD` 与应用连接串里的密码必须对应；特殊字符在连接串中需要 URL 编码。管理员密码与应用数据库密码分别生成。`postgres-init/10-app.sh` 仅在空数据卷首次初始化时创建应用账号和数据库，已有数据库改密码需要在库中执行变更。

Docker 的 `--env-file` 使用实际 `KEY=value`，不写 `export`、不额外包引号、不依赖 shell 变量展开；采用 UTF-8 无 BOM 和 LF 换行，权限设为 `600`。真实配置与含凭据发布包按私有文件管理，分享接入文档时只携带占位符模板。

## 7. 构建离线包，通过堡垒机部署

下面的构建命令以 Linux / WSL 的 Bash 和 GNU 工具为例，在项目根目录执行。构建机需要网络及可运行 `linux/amd64` 容器的 Docker；目标服务器不需要在线安装这些依赖。其他 CPU 架构必须重新准备对应引擎和镜像。

### 7.1 构建和导出应用镜像

先完成接入代码和本地验证，核对 `git status`。镜像构建使用当前工作区，包含 `.dockerignore` 允许的未提交文件；如果发布未提交快照，需要额外记录快照清单，不能只把基准提交号当成完整版本。

```bash
set -euo pipefail
umask 077
release_id=20261025-1
bundle_dir="dist/jht-jishi-offline-${release_id}"

git status --short
mkdir -p dist
mkdir "$bundle_dir"
mkdir -p "$bundle_dir/images" "$bundle_dir/engine" "$bundle_dir/config" "$bundle_dir/postgres-init"

docker build --platform linux/amd64 -t "jht-jishi:${release_id}" .
docker pull --platform linux/amd64 postgres:17-bookworm
docker run --rm --pull=never --network=none --entrypoint python \
  "jht-jishi:${release_id}" /app/deploy/container_smoke.py

docker save "jht-jishi:${release_id}" | gzip > "$bundle_dir/images/jht-jishi.tar.gz"
docker save postgres:17-bookworm | gzip > "$bundle_dir/images/postgres.tar.gz"

cp deploy/install-docker-offline.sh deploy/start-offline.sh deploy/reset-admin.sh "$bundle_dir/"
cp deploy/postgres-init/10-app.sh "$bundle_dir/postgres-init/"
sed -i 's/\r$//' "$bundle_dir"/*.sh "$bundle_dir"/postgres-init/*.sh
sed -i "s/jht-jishi:20261018/jht-jishi:${release_id}/g" "$bundle_dir/start-offline.sh"

git rev-parse HEAD > "$bundle_dir/SOURCE-COMMIT.txt"
docker image inspect --format 'ID={{.Id}} OS={{.Os}} ARCH={{.Architecture}}' \
  "jht-jishi:${release_id}" postgres:17-bookworm > "$bundle_dir/IMAGE-INFO.txt"
cp url-agent接入AI集市与部署.md "$bundle_dir/"
```

`start-offline.sh` 在当前仓库仍固定使用历史标签 `20261018`，因此制作新包时要同步替换包内副本。应用镜像不包含 `.env`、业务数据库、用户素材和本机虚拟环境。`storage/` 是代码，要保留；`data/` 是运行数据，要独立持久化。

从单位批准的软件库或可信来源取得并核验 Linux x86_64 的 `docker-28.5.2.tgz`，放入 `engine/`；路径占位符改成实际文件位置：

```bash
cp /path/to/approved/docker-28.5.2.tgz "$bundle_dir/engine/"
```

随后按第 6 节在 `$bundle_dir/config/` 新建并填写 `app.env` 和 `postgres.env`。更换引擎版本时同时修改安装脚本；保留已测试的数据库镜像及其 ID，应用升级不要顺带更新数据库版本。

### 7.2 校验和封装

配置保存完成后，在同一构建终端执行：

```bash
chmod 600 "$bundle_dir/config/app.env" "$bundle_dir/config/postgres.env"
(
  cd "$bundle_dir"
  sha256sum images/*.tar.gz engine/*.tgz *.sh postgres-init/*.sh \
    SOURCE-COMMIT.txt IMAGE-INFO.txt url-agent接入AI集市与部署.md > SHA256SUMS
  sha256sum -c SHA256SUMS
)
tar -czf "dist/jht-jishi-offline-${release_id}.tar.gz" \
  -C dist "jht-jishi-offline-${release_id}"
(
  cd dist
  sha256sum "jht-jishi-offline-${release_id}.tar.gz" \
    > "jht-jishi-offline-${release_id}.tar.gz.sha256"
)
```

内部清单校验镜像和固定脚本，排除允许按环境修改的 `config/*.env`；整包 SHA-256 覆盖初次上传的全部内容。交付 `.tar.gz` 和对应 `.sha256` 两个文件。

源码 ZIP 只适合留档或另行构建，不能代替包含镜像、引擎和配置的离线运行包。离线安装完成后，模型生成仍依赖可达的模型服务。

### 7.3 上传到目标资产

1. 连接 aTrust，进入 JumpServer，核对要部署的真实服务器和登录账号。
2. 在该资产的文件管理 / SFTP 上传整包及校验文件，例如上传到 `/root/jht-upload/`。
3. 确认文件管理和终端连接的是同一资产。若提示 `Connection websocket closed`，先重连。
4. 上传大小受限时分片传输，服务器合并后核对原整包 SHA-256。

JumpServer 地址不是应用访问地址；这一步完成的是文件传输。目标服务器 Bash 中执行：

```bash
set -euo pipefail
release_id=20261025-1
cd /root/jht-upload
sha256sum -c "jht-jishi-offline-${release_id}.tar.gz.sha256"
mkdir -p /opt/jht-jishi/releases
mkdir "/opt/jht-jishi/releases/${release_id}"
tar -xzf "jht-jishi-offline-${release_id}.tar.gz" \
  -C "/opt/jht-jishi/releases/${release_id}"
cd "/opt/jht-jishi/releases/${release_id}/jht-jishi-offline-${release_id}"
sha256sum -c SHA256SUMS
```

校验失败就停止，检查版本或重新上传。每个版本使用新的发布目录，避免覆盖已有部署。

### 7.4 安装引擎并首次启动

历史目标机原有 Podman 版本较旧，本项目使用独立的 `jht-docker.service`。首次安装需 root 权限，在上一步发布目录执行：

```bash
bash install-docker-offline.sh
systemctl status jht-docker --no-pager
jht-docker info
```

已存在且正常运行时跳过安装。这个引擎的 socket 是 `/run/jht-docker.sock`，数据目录为 `/var/lib/jht-docker`，后续统一使用 `jht-docker` 命令。若其他项目复用系统 Docker，需要统一调整脚本；不要混用两个引擎的命令、容器和数据卷。

确认配置后首次启动：

```bash
bash start-offline.sh
jht-docker ps
curl -fsS http://127.0.0.1:8000/api/health
bash reset-admin.sh
```

脚本检查架构相关运行依赖、FFmpeg / 中文字体、端口和容器冲突，导入本地镜像并创建持久卷，先启动数据库，再等待应用健康检查。容器使用 `--pull=never`，不会在启动时拉取远程镜像。

应用和数据库都使用 `unless-stopped`，引擎随系统启动。脚本不自动放通主机防火墙或配置网页代理。数据库只监听服务器本机，应用目录使用 UID/GID `10001:10001`；若改为主机目录挂载，应保证该用户可写。

`reset-admin.sh` 只重置 `admin` 并撤销旧管理员会话；本项目另有预设成员 `dingyufu`，上线前也需完成其凭据处理或停用安排。集市映射用户与这些本地账号独立。

脚本发现容器已存在时会退出。已有容器只是停止时可使用 `jht-docker start jht-postgres jht-app`；应用更新应按第 10 节执行，不重复运行首次安装流程。

## 8. 发布 HTTPS，并在集市验证入口

### 8.1 配置反向代理

正式集市父页面为 HTTPS 时，应用 iframe 也应为 HTTPS。项目可使用单位统一网关或 Nginx；下面是同机 Nginx 示例，证书路径、端口和域名均按实际环境替换。

本项目使用根路径及专用端口，前端包含 `/api`、`/uploads` 等绝对路径；若改为 `/myapp/` 子路径发布，需要同步适配前后端资源和路由。

```nginx
# 放在 nginx 的 http 配置作用域中。
# 日志使用 $uri，避免记录包含临时媒体凭据的查询串。
log_format app_without_query '$remote_addr [$time_local] '
    '"$request_method $uri $server_protocol" $status $body_bytes_sent';

server {
    listen 4193 ssl;
    server_name aimarket.hncig.cn;
    ssl_certificate     /etc/nginx/certs/app.fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/app.key;
    access_log /var/log/nginx/jht-access.log app_without_query;

    client_max_body_size 200m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 360s;
        proxy_send_timeout 360s;
        proxy_buffering off;
    }
}
```

配置要点：

- 保留外部 Host 的端口，因此示例使用 `$http_host`。代理在其他服务器时，上游改为应用服务器地址，同时配置准确的 `FORWARDED_ALLOW_IPS`。
- 正常转发 `Origin`、`Sec-Fetch-Site`、`X-JHT-Market-Session` 和 Range 请求，不替客户端伪造来源头。
- 上传上限和超时按业务调整；本项目音频上游超时可达 300 秒。
- 不在代理全站附加 `X-Frame-Options: DENY` 或 `SAMEORIGIN`，否则会阻止集市嵌入；也不要无条件删掉全站防嵌入策略。
- 下面的精确嵌入响应头由应用按路由产生。多层代理分别检查，避免上层又追加冲突的 CSP / X-Frame-Options。

### 8.2 iframe 与登录请求的响应策略

本项目仅对 `/market` 和需要的内层画布允许集市祖先，响应包含：

```http
Content-Security-Policy: frame-ancestors 'self' https://aimarket.hncig.cn:4090
```

这些特定页面移除冲突的 `X-Frame-Options`；普通独立页面继续保留自己的防嵌入策略。CSP `frame-ancestors` 检查实际祖先链，不能只处理最外层页面而忽略内层 iframe。

iframe 内部 fetch 自己的后端仍是应用同源请求，`Origin` 应为应用的 `4193` 来源，并非集市父页面的 `4090` 来源。因此无需为这种接入放开全站 CORS；如果选择浏览器直接请求模型网关，则网关另需允许实际应用 origin 的 CORS，该流程不同于本文的后端模型请求方案。

SDK 免登录不依赖第三方 Cookie。本地密码登录 Cookie 默认 `SameSite=lax`，不能通过随意改成 `none` 解决所有 iframe 问题。代理终止 TLS 时，还需让后端正确识别 HTTPS，保证本地登录 Cookie 的协议属性正常。

代理端先检查配置，通过后再重载：

```bash
nginx -t
systemctl reload nginx
```

然后在 AI 集市中登记或核对应用地址 `https://aimarket.hncig.cn:4193/market`，从平台页面进入。直接打开这个 URL 可以检查 HTML 是否可达，但无法替代 SDK 父页握手验证。

## 9. 按层联调和验收

### 9.1 先检查应用和代理

在应用服务器执行：

```bash
jht-docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
jht-docker logs --tail 100 jht-app
jht-docker exec jht-postgres pg_isready -h 127.0.0.1 -p 55432 -U postgres -d postgres
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/integrations/aimarket/config
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/projects
```

预期健康返回 `status=ok`、`storage=PostgreSQL`，公开配置显示正确的 origin 和应用 ID，匿名项目接口返回 `401`。本项目 `/api/health` 不会每次实时查询数据库或模型服务，`pg_isready` 也不代替应用账号读写验收。

在能够访问发布域名的网络中检查 HTTPS 与页面响应头：

```bash
curl -fsS https://aimarket.hncig.cn:4193/api/health
curl -fsS https://aimarket.hncig.cn:4193/api/integrations/aimarket/config
curl -sS -D - -o /dev/null https://aimarket.hncig.cn:4193/market
```

使用符合环境要求的受信任证书；检查响应头时使用 GET，避免只实现 GET 的页面因 HEAD 返回 405 被误判为不可用。

### 9.2 从真实集市完成业务验收

| 验收项 | 操作 | 通过标准 |
| --- | --- | --- |
| 父页与嵌入 | 从平台打开登记的应用 | 页面和静态资源正常，无 mixed content 或 frame 拦截 |
| SDK 握手 | 正常进入；测试无效来源、请求编号与应用 ID | 正常连接成功；不匹配消息不会创建有效业务会话 |
| 免登录 | 使用未登录本地账号的浏览器进入 | 获得当前集市用户，不要求本地密码 |
| 启动顺序 | 查看浏览器网络请求顺序 | 先成功创建绑定，再请求用户和受保护业务接口 |
| 身份独立 | 另一标签页登录本地管理员后再进入集市 | 集市仍显示平台用户，不继承管理员权限 |
| 用户隔离 | 两名平台用户分别创建项目并上传素材 | 不互相访问私人项目、任务和媒体；刷新后身份稳定 |
| 数据持久化 | 创建并保存项目、刷新、重启应用 | 数据保留；重启后重新连接可继续访问 |
| 媒体读取 | 播放视频、拖动进度、预览图片、下载文件 | 业务 Header 和媒体 token 各自生效，Range 播放正常 |
| 模型调用 | 用约定测试额度调用所需模型 | 请求、异步查询、媒体下载、保存和显示完整成功 |
| 凭据失效 | 撤销会话、等待过期或重启服务 | 拒绝旧凭据，提示重连，不改用其他用户 Key |
| 长任务 | 退出并重新进入，检查已有供应商任务 | 在授权和可恢复状态下继续查询；不无条件重新 POST |
| 运维交接 | 核对版本、日志、配置、卷和备份位置 | 可以定位运行代码与数据，具备恢复方案 |

模型联调应使用约定的测试额度。健康检查的 `provider_configured` 字段不能单独判断集市模式是否可用，因为用户进入后才取得 SDK Key。

浏览器调试只记录阶段、HTTP 状态码、脱敏错误和请求编号；不要输出完整 context、Key 或含临时 token 的截图与日志。

### 9.3 本项目可参考的回归检查

以下是已有项目的验证入口，使用临时数据和模拟平台 / 供应商。它们不代替真实集市和服务器联调，也不是本次编写文档已经重新执行的测试结果。

```bash
# Linux 开发环境；不要向测试进程注入正式数据库连接。
.venv/bin/python -m unittest discover -s tests -p 'test_market_identity.py'
.venv/bin/python -m unittest discover -s tests -p 'test_market_sdk_login.py'
.venv/bin/python -m unittest discover -s tests -p 'test_proxy_origin.py'
.venv/bin/python -m unittest discover -s tests -p 'test_aimarket_profile.py'
node --test tests/market_session.test.mjs tests/market_entry.test.mjs
```

Windows 把 Python 路径换为 `.venv/Scripts/python.exe`。浏览器测试还包括 `tests/market_login.browser.cjs`、`tests/market_proxy.browser.cjs` 和 `tests/market_generation_recovery.browser.cjs`，需要单独准备其 Playwright / 浏览器测试环境。

## 10. 上线后的配置变更、备份和故障处理

### 10.1 配置变更与发布规则

- 修改 `config/app.env` 后，单纯 `jht-docker restart jht-app` 不会读取新环境文件；需要保留数据卷并重建应用容器。
- 新应用镜像使用新的版本标签，升级前等待任务结束并备份，保留旧容器及配置。沿用现有数据库密码，不能用新安装模板生成的密码连接旧数据库。
- 同一正式数据库只运行一个应用实例。旧容器保留作回滚时保持停止，并关闭其自动重启。
- 重建应使用包含全部当前代码的镜像。历史 `apply.sh` 补丁位于容器可写层，用旧镜像重建会丢失补丁。
- 代码回滚不会撤销数据库和媒体变化。涉及不兼容数据迁移时，恢复到新库和新卷，验证后再切换。
- 备份同时覆盖 PostgreSQL 逻辑备份、应用媒体卷、服务器配置和应用版本记录；数据库单库备份不包含集群角色，跨机恢复需另建应用账号及授权。

本项目完整的备份、恢复、应用容器替换和回滚命令见[部署.md](部署.md)第 8、9 节。恢复演练先隔离真实供应商出口，防止恢复库中的未完成任务自动发起真实请求。日常升级保留数据卷，不通过删除卷重新安装来处理故障。

### 10.2 常见问题

| 现象 / 错误 | 优先检查 | 对应处理 |
| --- | --- | --- |
| `NOT_EMBEDDED` | 是否直接打开 `/market` | 从集市 iframe 进入；直达页面只用于检查可达性 |
| `INVALID_OPTIONS` | `marketOrigin` 是否含路径、斜杠或默认端口的非规范写法 | 使用浏览器准确的 `location.origin` 形式 |
| `TIMEOUT` | 父页是否接入协议，origin、父窗口层级和 requestId 是否一致 | 查父页握手响应；不要盲目重试模型调用 |
| `CONNECT_REJECTED` | 平台是否拒绝本次授权或 context 获取 | 由平台检查用户、应用与授权状态 |
| `INVALID_CONTEXT` | 字段类型、`accountType`、`organization` 和 Key 格式 | 按 SDK 契约返回字段，无值字段使用 `null` |
| `AGENT_MISMATCH` / 登录 403 | 平台登记 ID、前端配置和后端 ID | 全部改成当前项目自己的应用 ID |
| 进入集市仍要求本地密码 | SDK 登录开关、启动先后顺序、入口路径 | 使用 `/market`，启用免登录，绑定成功后再读用户 |
| iframe 被拒绝或 mixed content | HTTPS、CSP、X-Frame-Options、平台 sandbox | 检查每层代理与页面的实际响应和嵌入权限 |
| 写操作 403“请求来源无效” | PUBLIC_ORIGIN、Host / 协议 / 端口、浏览器来源头 | 对齐应用外部 origin，检查可信代理；不放开所有来源 |
| 业务接口 428 | 绑定是否过期、退出或因重启丢失 | 原用户重新连接，清理旧状态，不自动重发未知生成 |
| API 正常但图片、视频 401 | 媒体是否携带有效 `jht_media`、归属是否匹配 | 在展示阶段生成媒体 URL，保持读取与业务授权范围独立 |
| 模型 401 / 403 / 额度不足 | 当前会话 Key、模型权限和供应商错误 | 检查当前账号授权，不能改用另一个账号的 Key |
| 模型配置可见但提示来源不匹配 | 保存的模型网关与部署允许 origin | 核对模型管理和 `MARKET_MODEL_ORIGIN` |
| 文本正常，音频失败 | 音频 Key 和独立接口网络 | 核对 `SEEDAUDIO_API_KEY` 与音频服务出口 |
| 视频完成但下载失败 | 实际素材 URL、签名有效期及服务器网络 | 单独排查下载链路，保持 URL 签名，不关闭全部地址校验 |
| 反向代理 502 / 504 / 上传 413 | 应用日志、监听地址、上游路由、超时和请求体上限 | 分层定位后端、网络或代理限制 |
| env 已修改但行为不变 | 容器是否重新创建、配置是否被显式环境覆盖 | 检查实际发布配置，按升级流程重建 |
| 重启后间歇性掉登录 | 是否配置了多个 worker 或副本 | 按当前架构保持单实例、单 worker |

## 11. 给其他项目复用时的改造清单

| 工作项 | 可以参照的本项目实现 | 新项目需要完成的内容 |
| --- | --- | --- |
| 平台登记 | 本文第 2 节 | 新应用 ID、HTTPS URL、模型权限、身份契约 |
| 浏览器 SDK | `public/vendor/aimarket.js` | 使用平台提供的匹配版本，不另造消息协议 |
| 接入页面 | `/market`、`public/app.js` | 可加载接入壳，先完成应用会话，再请求业务数据 |
| 前端状态 | `public/market-session.js` | 连接、失败、重连、退出、会话失效及嵌套页面处理 |
| 后端入口 | `market_service.py` | 自己框架的 config、login、撤销与模型凭据解析接口 |
| 身份与权限 | `market_identity.py`、`auth_service.py` | 按平台稳定身份映射账号，落实业务与文件所有权 |
| 媒体访问 | `marketMediaUrl()` 与媒体后端鉴权 | 解决普通媒体标签、Range 与临时凭据清理 |
| 模型适配 | `provider_adapters.py`、`model_gateway.py` | 项目需要的模型、路由、鉴权、参数和异步任务协议 |
| 服务配置 | `config/app.env` 模板 | 更换全部项目专属变量、域名、端口和数据库参数 |
| 镜像构建 | `Dockerfile`、`.dockerignore` | 使用新项目实际运行时与入口；Vue / React 等按自身流程构建前端 |
| 容器与持久化 | `deploy/start-offline.sh` | 替换镜像、容器、卷、端口、UID、数据库和健康检查 |
| 管理员初始化 | `reset-admin.sh` 等 | 使用新项目自己的账号初始化和凭据管理方案 |
| HTTPS 发布 | 第 8 节 | 证书、代理来源、Origin、嵌入响应头和访问策略 |
| 验收与交接 | 第 9、10 节 | 本项目业务场景、备份恢复、升级回滚和运行记录 |

如果新项目是 Java、Node.js 或其他后端，只需按相同接口职责实现自身服务；不要求采用 Python、PostgreSQL 或建绘通的 Header 命名。移植部署脚本前要同步替换其中的容器、端口、数据库、持久卷、应用路径和检查命令，仅改包名不足以完成适配。

当前 SDK 只定义 connect / context / error 消息。若新项目还需要向集市回传业务结果、进度、自动调整 iframe 高度或实现统一退出，需要与平台约定额外协议，不能假定 SDK 已经包含这些功能。

发布时保留一份不含明文凭据的交接表：

```text
项目名称 / 应用 ID：
集市父页面 origin：
应用公开 origin / 完整入口 URL：
模型网关 / 可用模型与接口：
身份信任或验真方式：
目标服务器 / 架构 / 代理位置：
发布版本 / 代码提交或快照 / 镜像 ID / 包 SHA-256：
容器、端口、数据卷、数据库：
配置与凭据保管位置：
备份位置 / 恢复验证结果 / 回滚限制：
集市登录、用户隔离、模型、媒体与持久化验收结果：
维护人员 / 发布日期：
```

## 12. 本项目参考文件

下列链接相对于项目根目录。单独分享本文时，可按文件路径回到原项目查阅；主要接入与首次部署流程已包含在本文中。

- [SDK 实现](public/vendor/aimarket.js)、[前端会话适配](public/market-session.js)、[前端启动](public/app.js)。
- [集市后端服务](market_service.py)、[平台账号映射](market_identity.py)、[认证与来源校验](auth_service.py)。
- [模型网关切换](model_gateway.py)、[供应商协议适配](provider_adapters.py)。
- [SDK 接入说明](docs/aimarket-sdk.md)、[AI 集市模型及配置说明](docs/aimarket-jishi-deployment.md)。
- [Dockerfile](Dockerfile)、[容器入口](deploy/container_entrypoint.py)、[首次启动脚本](deploy/start-offline.sh)、[离线引擎安装](deploy/install-docker-offline.sh)。
- [完整服务器运维流程](部署.md)、[数据库部署与迁移](docs/database-deployment.md)。
- [任务队列与重启恢复](docs/generation-queue.md)、[2026-10-23 R2 更新记录](docs/server-update-20261023-r2.md)。
