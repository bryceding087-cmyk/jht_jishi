# 建绘通 · AI 视频创作平台

项目源代码采用 [MIT License](LICENSE) 开源。随附的第三方依赖和资源遵循各自许可证；模型服务需自行配置访问凭据和使用权限。仓库不包含部署密钥、账号数据库、用户项目或生成素材。

近期更新：支持 [AI 集市 Wan 3.0](docs/wan3-aimarket-20261031.md)、[视频文件恢复](docs/video-file-recovery-20261031.md)、[轮询闪烁修复](docs/polling-stability-20261031.md)、[统一视频加载动画](docs/triangle-video-loading-20261031.md)、[画布超时状态修复](docs/canvas-timeout-state-20261031.md)和[视频参考图片压缩](docs/video-reference-compression-20261031.md)。

此目录为 AI 集市接入版本。默认文本模型为 `deepseek-v4.1-flash`，图片模型为 `doubao-seedream-5.0-pro`，视频模型为 `minimax-h3`；三者使用 AI 集市网关及 SDK 下发的当前会话 Key。音频使用火山引擎 `seed-audio-1.0` 和独立服务端 Key。部署入口、环境变量、接口映射及验证边界见 [AI 集市版本部署说明](docs/aimarket-jishi-deployment.md)。

通过 `npm start` / `server.py` 本地启动时，文本、图片和视频模型默认使用 `https://aimarket.hncig.cn:4090`；Docker 镜像和容器入口默认使用服务器内网网关 `http://192.168.241.202:4091`。配置项为 `JIANHUITONG_MARKET_MODEL_ORIGIN`，显式进程环境变量优先于 `.env`。默认模型、AI Market 服务商预设及保存过的这两个网关地址会在读取时统一到当前环境；其他自定义地址和音频接口保持各自配置。

建绘通是湖南建投内部使用的 AI 视频创作平台，使用 Python / FastAPI 提供后端服务，正式运行使用 PostgreSQL 和 SQLAlchemy 保存账号、会话、项目、任务及素材元数据。媒体文件保存在 `data/uploads/`；SQLite 仅保留为旧数据迁移来源和隔离测试数据库。正式启动缺少 PostgreSQL 配置时会明确报错，不会自动回退到 SQLite。

## Agent 与剧本工作台

创作请求支持[停止生成](docs/generation-stop.md)：发送按钮在请求期间切换为停止按钮，并带轻微图标动效。停止保留草稿和已完成结果，阻止迟到提交、后续步骤及自动回填；已经提交给模型供应商的请求可能继续处理。

图片与视频生成已接入[按用户隔离的持久任务队列](docs/generation-queue.md)：图片不限制应用层并发，视频每个账号最多并发 10 个、全系统最多并发 100 个，超出的任务排队等待。首页、画布和 Agent 共用队列；后台持续调度，可通过队列接口查询和取消未提交的任务。图片／视频对话允许连续提交，重启恢复不会自动重复请求结果未知的任务。

管理员可在左侧「系统日志」查看[用户操作、生成请求与任务队列](docs/admin-logs.md)，按账号、时间和状态筛选，并追踪请求编号、供应商任务 ID 及状态变化。操作与变化日志从功能启用后开始记录，已有生成任务可直接查询。

首页 Agent 已接入普通问答、生图、生视频、剧本创作和一句话短剧分流。新剧本工作台按「故事设定 → 分集大纲 → 正文 → 资产与镜头 → 制作画布」推进，支持稿件编辑与版本历史。画布使用真实素材引用，按集分组；可以逐节点生成，也可以点击「制作成片」连续生成缺少的素材、镜头及分集／整剧视频。界面沿用建绘通风格，详见[Agent 与幕间剧本工作台](docs/agent-studio.md)。

## 原有对话式视频创作 Agent

[视频创作 Agent](agents/video-creation/README.md)保留用于已有任务与 `workflow: "video"` 流程：输入故事后，依次完成导演检查、时长分析、创意与素材、音色参考、分镜、正式配音与整片 BGM、分镜视频及最终合成。界面支持分页确认卡、工具进度、文档和媒体预览、创作记录、引用素材及项目画布联动。

主控在 [agent_runtime.py](agent_runtime.py)，合成器在 [agent_media.py](agent_media.py)，UI 在 [public/agent-chat.js](public/agent-chat.js)。[主控定义](agents/video-creation/AGENT.md)及 16 个子 Skill 通过 [agent.yaml](agents/video-creation/agent.yaml)加载，实际业务调用全部在建绘通服务内完成。支持仅做分镜、主动润色、暂停恢复、修改具体镜头和仅改片尾；保留原稿、文档版本、任务回执及有效媒体。

Agent 从故事分析开始读取所选模型的实际时长、画幅、分辨率和参考输入约束；[规划适配器](agent_planning.py)自动调整不支持的时长、拆分超长镜头，并根据正式配音实测时长添加连续画面，完整保留台词。镜头修改采用同一规则。已收到但内容校验失败的文本最多自动修正两次；结果未知的请求不自动重发。AI Market 的 minimax-h3 会将本地参考图片编码后随视频请求提交，Agent 保留实际图片参考。

Agent 与手动创建的画布共用节点和连线样式。画布展示采用故事、分镜脚本、实际素材、每镜一个视频节点及最终成片；过程分析、自审、时间线等文档在对话中查看，不投放为 JSON 文本节点。镜头开始执行时才创建视频节点，生成结果回填同一节点。旧画布打开时会合并重复结果卡、移除流程展示元素，保留真实媒体、用户编辑的文本及手动位置。

打开或保存分镜时，编辑器补齐的默认字段和等价提示词展示不会使 Agent 的生成确认失效。输入校验比较规范化后的有效分镜、模型参数与参考素材；旧版确认记录也可继续使用，已成功片段会复用。实际修改提示词、镜头时长、模型、画幅或参考图仍需重新确认。

完整视频需要配置可用的文本、图片、视频和音频模型。最终拼接、混音、字幕与片尾由本地 FFmpeg 完成；安装 requirements.txt 会安装随包的 FFmpeg，也可用 JIANHUITONG_FFMPEG 指定。供应商费用暂不能统一查询，字幕按正式台词音频区间排布，未接入口型同步或自动画面审查，成片由用户播放确认。

当前运行引擎使用单个 Uvicorn 进程内后台线程，请保持单 worker 部署。API、状态、幂等及恢复边界见 [运行说明](agents/video-creation/contracts/runtime.md)。

Agent 在理解故事、设计创意、确认分镜和续作时，会结合当前作品给出简短的创作说明与下一步安排。整片合成使用前置生成的 BGM，关闭片段自带音轨，正式配音独立混入；无配音时提高配乐音量，音乐在整片结尾淡出。

## 在另一台电脑上启动

安装 Git、Python 3.11 或更新版本，以及 Node.js 18 或更新版本（包含 npm）。本仓库公开，可直接克隆。前端静态文件由同一个 Python 服务提供，无需单独构建前端。

Windows（首次本地数据库安装使用 PowerShell 7）：

```powershell
git clone https://github.com/bryceding087-cmyk/jht_jishi.git
cd jht_jishi
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
pwsh -NoProfile -File .\scripts\setup_local_postgres.ps1
npm ci
npm start
```

若 Python 安装未提供 `py` 命令，将第三行换成 `python -m venv .venv`。无需激活虚拟环境；启动器会优先使用项目内 `.venv` 的 Python（显式设置 `PYTHON` 环境变量时则使用指定解释器）。数据库安装脚本只在本项目 `.runtime/` 和 `data/` 建立 PostgreSQL 17.11 实例，监听 `127.0.0.1:55432`，随机凭据写入忽略的本地配置。完成安装后只需 `npm start`：启动器由 Python 直接调用 `pg_ctl`，不依赖 Windows PowerShell 的脚本执行策略，也不修改系统策略。若使用已有 PostgreSQL，在 `.env` 配置连接即可，启动器不会管理外部数据库进程。

已有 `data/jianhuitong.db` 时，应在第一次 `npm start` 前按[数据库部署与迁移](docs/database-deployment.md)导入空 PostgreSQL 数据库；新安装可直接启动。

macOS / Linux：

```bash
git clone https://github.com/bryceding087-cmyk/jht_jishi.git
cd jht_jishi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# 配置已安装或托管的 PostgreSQL，将连接写入 .env。
npm ci
npm start
```

打开 [http://127.0.0.1:8000/](http://127.0.0.1:8000/)。首次启动会在配置的 PostgreSQL 中初始化应用表，并创建素材目录 `data/uploads/`。管理员登录后，在左侧「模型管理」按需配置文本、图片、视频和音频模型的 API Key，普通成员选择使用。

开发时可通过同一启动器开启 Python 自动重载，先停止已有应用进程，避免端口冲突：

```powershell
npm start -- --reload
```

修改前端 HTML / CSS / JavaScript 后刷新浏览器。`server.py` 读取根目录 `.env`；显式设置的进程环境变量优先。`node server.js --check` 可检查启动配置，不启动应用或修改数据库。直接导入 `app` 的隔离测试不会自动加载部署 `.env`。

## 视觉风格

图片、视频和 Agent 创作已支持 visual-style-studio 2.3.0 风格库，可按真人／2D／3D／建筑分类选择 152 种带参照图的风格，也可自定义或清除。每次生成保存所选风格并自动将正式配方加入模型提示词，详见[视觉风格选择与验证](docs/visual-styles.md)。

## 登录与账号

打开首页后，未登录的用户会自动进入 `/login`。预设账号为 `admin`（平台管理员）和 `dingyufu`（创作成员），初始密码按部署时约定配置；账号仅在首次初始化时创建，重启不会覆盖已有密码。普通成员只能查看和管理自己的项目、资产、对话与生成任务；管理员也必须输入账号密码登录，进入同一个系统后可管理全部用户数据。模型和 API Key 由管理员统一配置，普通成员只能选择使用。无法确认归属的历史数据仅管理员可见，详见[用户隔离说明](docs/user-isolation.md)。

登录支持密码显示切换、错误提示和「7 天内保持登录」。不勾选时使用浏览器会话 Cookie，服务端会话最长有效期为 12 小时；勾选后最长为 7 天。侧栏底部显示当前账号，可点击退出按钮；退出前会等待画布保存，退出后服务端立即注销会话。账号密码以独立盐值的 PBKDF2 哈希存储，会话令牌仅通过 HttpOnly Cookie 传输，数据库只保存令牌哈希。业务接口、页面和上传素材均需要有效登录；连续失败登录受频率限制。

认证接口：`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/logout`。脚本调用写接口时需携带与服务地址一致的 `Origin` 请求头及登录 Cookie。认证回归测试使用临时数据库，不修改本地业务数据：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_auth.py" -v
```

## Git 版本管理与数据迁移

仓库保存源码、依赖清单和测试。`.gitignore` 排除了本地数据库、模型密钥、上传与生成素材、依赖目录、临时文件和运行日志；克隆代码不会带走已有画布、作品或模型密钥。

迁移到另一台电脑时，按[数据库部署与迁移](docs/database-deployment.md)使用 PostgreSQL 逻辑备份恢复账号和业务记录，并单独复制 `data/uploads/`。旧版 SQLite 使用专用导入脚本迁移。数据库备份包含业务数据与模型密钥，仅通过可信方式保存和传输，不加入 Git。不要将运行中的 PostgreSQL 数据目录当作普通文件夹直接覆盖到另一台机器。

## URL Agent SDK 接入

已接入提供的 `url-agent/aimarket.js`，保留 SDK 原始协议实现。外部系统使用 `/market` 作为 iframe 入口，通过精确配置的父页面 origin 握手；普通独立入口和内部嵌入画布保持各自会话流程。配置项、消息校验、凭据生命周期与验收方式见[URL Agent 接入说明](docs/aimarket-sdk.md)。

集市入口 `/market` 已按确认的 SDK context 契约实现免登录：以集市 origin + user.id 映射个人账号，apiKey 用于当前用户模型请求。集市会话不依赖第三方 Cookie；独立 `/login` 保留本地密码登录。服务端不额外核验平台签名，详情见 [集市接入说明](docs/aimarket-sdk.md)。

开始开发前更新代码，完成后提交并推送：

```bash
git pull --ff-only
git status
git add .
git commit -m "描述本次修改"
git push
```

多人协作或较大改动可用 `git switch -c feature/功能名称` 创建分支。首次在新电脑提交前，设置该仓库的提交署名与邮箱（可使用 GitHub 的隐私邮箱）：

```bash
git config user.name "你的 GitHub 用户名"
git config user.email "你的 GitHub 隐私邮箱"
```

## 回归检查

安装依赖后运行后端单元测试和前端脚本语法检查，无需调用真实模型接口：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
node --check public/app.js
node --check public/audio-generation.js
node --check public/asset-library.js
node --check public/agent-chat.js
```

macOS / Linux 将 Python 路径替换为 `.venv/bin/python`。浏览器交互验证可使用 Playwright 和独立测试数据库，不属于运行依赖。

## 居中弹窗动效

登录、首次启动、模型、历史对话和生成窗口使用共享加载动效。项目和资产列表已移除整页加载动画，图片和视频封面进入可视区域后才请求加载；读取失败仍显示重试提示。短请求延迟显示，后台轮询使用业务进度。后续页面接入方式与慢网验证见[页面加载状态](docs/loading-states.md)。

所有居中模态弹窗统一从触发位置缩放至页面中心，支持鼠标与键盘打开，并遵循系统“减少动态效果”设置。新功能使用现有 `showModal`、原生 `<dialog>`，或 `role="dialog" aria-modal="true"` 时默认带动画。自定义组件接入及覆盖范围见 [弹窗动效约定](docs/modal-motion.md)。

## 画布撤销与重做

画布节点、连线与分组编辑共用一条按操作顺序排列的历史，最多保留当前会话的 80 步。`Ctrl+Z` 撤销，`Ctrl+Shift+Z` 或 `Ctrl+Y` 重做；macOS 可用 Command。节点及分组的新增、移动、重命名、删除、排列，以及分组颜色和边界编辑均按时间顺序记录；同一次操作中的节点、连线和资源绑定一起恢复。撤销后进行新编辑会清除旧重做分支。视角、任务进度与媒体尺寸加载不参与撤销，输入框保留文字编辑的原生撤销。切换项目、刷新页面或载入 Agent 的新画布快照时重置历史。

实现位于 `public/canvas-history.js`，图编辑通过 `scheduleSave` 在同一事件内合并记录。校验：Node 20+ 执行 `node tests/canvas_history.test.mjs`；浏览器回归执行 `node tests/verify_canvas_viewport.cjs --undo`，使用临时数据库，不调用模型接口。浏览器回归可通过 `PLAYWRIGHT_MODULE`、`BROWSER_CHANNEL` 和 `PYTHON` 指定本机运行环境。

## 已完成能力

- 无限画布：移动工具、抓手工具、Space 临时抓手、滚轮缩放、适应画布、节点拖拽和端口连线
- 画布界面：参照 Liblib 实际画布改造为全屏工作区、悬浮项目栏、底部工具条与节点附近的编辑面板；支持工作流／故事板切换、5%–800% 缩放、节点搜索、按连接整理画布、小地图、连线显隐、网格吸附和历史视频加入画布。设计说明与验证见 [画布改造记录](docs/liblib-canvas-redesign.md)
- 节点工作流：双击空白画布添加文本、图片、视频素材、音频、脚本五类基础节点，另有视频生成；人物参考使用图片素材。节点参数独立保存，视频输入由前置连线自动判断，参考素材与画布节点双向同步
- 文本润色：文本节点内一键调用已绑定的大模型润色，保留负面提示词，防止异步结果覆盖后续编辑
- 脚本节点：从上游文本节点生成可编辑分镜表、资源清单和逐镜头视频提示词；支持资源上传、Excel 分镜导入、模板下载与三步工作流
- 大模型接入：脚本节点内可绑定任意 OpenAI 兼容接口，API Key 仅保存在 Python 服务端并以脱敏状态返回
- 项目：按封面、名称和日期展示已创建的画布，支持搜索、文件夹、重命名、复制、移动、回收站和恢复；“开始创作”创建空白画布。原“我的作品”生成结果列表已移除。
- 资产：仅保存图片、视频和音频，以生成历史形式按创建日期分组；支持分类计数、时间排序、预览、下载、多选和引用保护删除。文本与脚本保留在画布或 Agent 创作记录，不再生成素材库副本。旧文本、脚本及角色资产在启动时清理，清理前自动保存数据库快照到 data/backups。角色参考图片保留为普通图片节点。
- 生成任务：无图片参考时使用文本输入，连接图片参考后使用图文输入；多张图片均作为参考图，任务异步提交和状态轮询
- 模型接入：默认 Seedance 2.0 Mini、用户自定义模型、服务端保存脱敏 API Key
- 模型管理：左侧统一管理文本、图片、视频和音频模型；每类支持多配置、编辑、设置默认、切换和删除，API Key 只返回脱敏状态。选择服务商会带出模型与接口预设，视频模型可分别配置提交路径和查询路径。画布、脚本、图片及音频生成弹窗会使用对应的模型配置。
- 作品管理：失败原因、内容安全拦截、任务重试、预览和视频下载

首次使用视频生成前，请在左侧「模型管理 → 视频生成模型」中选择服务商并配置对应的 API Key。默认使用火山方舟任务接口，也支持 AI Market；实际生成是否成功还取决于账号权限、模型额度和供应商返回状态。

### 画布分组

在移动工具下，从画布空白处拖动框选两个或更多节点，点击多选工具栏的「打组」，或按 `Ctrl/Alt + G`。Shift 点击节点可以增减多选。组使用半透明背景、外置名称和选中角标；拖动组名或组框空白可整体移动，双击组名或点击「重命名」可编辑名称。工具栏提供十种组颜色，以及宫格、水平、垂直三种组内排列。角标调整组框大小，不缩放媒体；组框保持包含成员节点。

组内节点仍能单独编辑、移动、播放和连线。点击「解组」或按 `Ctrl/Alt + Shift + G`，保留节点及连线。分组操作支持 `Ctrl+Z` 撤销及 `Ctrl+Shift+Z` 重做。删除选中的组会删除成员节点，撤销可一起恢复节点、连线和分组。组名、颜色、成员关系及边界随项目自动保存，刷新和复制项目均保留；删除成员会清理组成员列表，空组自动移除。「整理画布」把组视作整体，保留内部相对位置。macOS 可将 Ctrl 换成 Command。

分组元数据保存在项目的 `groups` 字段中，与节点、生成任务和参考连线独立；旧客户端更新项目时不会清空已有分组。交互回归：`.venv/Scripts/python.exe tests/verify_canvas_groups.py`，使用临时数据库，不调用模型服务。

### 视频节点的视频参考

将「视频素材」节点的输出端口连接到「视频生成」节点输入端口，即可引用其视频内容；也可以从参考素材选择器选择视频，或在提示词中通过 `@` 引用视频。已生成的视频节点提供「作为视频参考」操作，会将完成结果保存为素材并连接到新的生成节点。直接连线或 `@` 引用已生成节点同样会保存该结果，后端核对任务与素材绑定，防止把上游的参考图当作视频输出。上游重新生成后会更新已有引用；未完成或过期的结果会在提交时提示重新连接。

已接入 Seedance 2.0 系列、Seedance 2.5，以及 AI Market `minimax-h3`。视频按 `content[].type=video_url`、`role=reference_video` 提交，本地 MP4 使用 Base64 Data URI。默认最多 3 个视频、总时长 15 秒；Seedance 2.5 最多 10 个、总时长 30 秒。单段至少 2 秒，单个文件不超过 50 MB。每次提交读取实际媒体时长；MiniMax H3 的编码请求总大小仍限制在 64 MB。参考素材可独立断开，原视频节点会保留。不支持视频参考的模型会在创建任务前提示切换模型。

### AI Market 视频调用

添加视频模型时选择「AI Market」，会自动带出以下配置，模型 ID 与地址均可修改：

| 配置 | 默认值 |
| --- | --- |
| 模型 ID | `minimax-h3` |
| Base URL | 本地 `https://aimarket.hncig.cn:4090`；Docker `http://192.168.241.202:4091` |
| 提交路径（POST） | `/v1/video_generation` |
| 查询路径（GET） | `/v1/query/video_generation/{task_id}` |

提交和查询都使用 `Authorization: Bearer <API Key>`。生成任务始终读取所选模型配置中保存的服务商、模型 ID、地址与密钥。浏览器向本项目 `/api/tasks` 提交内部参数后，后端按 AI Market 协议发送 `model`、`content`、`resolution`、`duration`、`ratio`，将提示词放入 `content` 文本项、将 `aspect_ratio` 改为 `ratio`、将分辨率转换为 `768P` 等大写形式；方舟的 `generate_audio`、`watermark` 字段仅用于方舟协议。

AI Market 的 `minimax-h3` 使用 **768P / 2K、4～15 秒**。选择该模型后，视频节点只显示这些选项；已有节点中的 `720p` 会在加载参数时选为 `768p`，有效的 15 秒时长会保留。脚本节点仍决定镜头时长，超出模型范围时需先修改脚本。后端也会在创建任务前校验，拒绝不支持的分辨率或时长。该范围仅适用于 AI Market 的 `minimax-h3`，不会套用到其他服务商或其他模型 ID。图示调用示例 **768P、4 秒、16:9** 可用于对照请求格式。

查询路径中的 `{task_id}` 会替换为提交响应中的任务 ID，支持读取常见的 `data`、`output`、`result`、`raw_response` 嵌套结果，并将任务状态统一显示为排队、生成中、完成或失败。完成后沿用视频下载与本地缓存流程。当前适配要求查询结果包含可下载的视频 URL；只返回文件 ID 的接口还需要接入对应的文件下载协议。

AI Market 的 `minimax-h3` 支持本地图片参考：后端将 `/uploads/` 图片编码为 Base64 Data URI，放入 `content[].image_url.url` 随请求提交，也保留已有公网 HTTP(S) URL。每张本地参考图最多 10 MB，编码后的完整请求不超过 64 MB；缺失或删除的图片会在提交前报错。Agent 同样保留本地图片参考，不再降级为文字设定。其他 AI Market 模型仍使用公网 URL 参考协议。图片上传目录继续受登录保护，无需公开本地服务。MiniMax H3 官方协议参考：https://platform.minimax.io/docs/api-reference/video-generation-v2-create 。AI Market 网关兼容性仍以实际接口响应为准。

已有方舟配置会保留原来的提交地址，并自动补出 `提交路径/{task_id}` 查询路径。在同一服务商下编辑模型时，API Key 留空会保留原密钥；切换服务商会清除旧服务商密钥，需重新配置。`provider_adapters.py` 集中定义服务商预设、请求参数和查询响应适配规则。相关测试使用临时数据库与模拟响应，不调用收费接口：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_provider_adapters.py" -v
```

使用脚本节点时，在画布底部「添加节点」中加入「脚本生成器」，它会自动连接当前文本节点。打开节点后可在右上角「大模型设置」填写模型 ID、Base URL、接口路径和 API Key，然后依次完成确认镜头、准备资源、合成提示词三步；脚本大模型也可直接在左侧「模型管理 → 文本大模型」统一配置。也可以通过环境变量 `LLM_API_KEY` 或 `OPENAI_API_KEY` 提供默认脚本大模型 Key；环境变量只作为未保存服务端配置时的回退值。

在脚本节点「确认镜头」页点击「下载模板」，填写 `.xlsx` 文件后选择「导入 Excel」。模板列为镜号、时长、画面描述、景别、光影氛围、对白·旁白、音效、运镜、最终提示词。必须保留全部表头，可调整列顺序；每行必填唯一正整数镜号、正数秒数时长和画面描述，其余字段可留空。最多 80 个镜头、文件不超过 5 MB，不支持公式或加密文件。

导入先显示预览，确认后可追加到现有镜头或替换分镜，内容自动保存并可继续编辑。替换保留已上传资源，但清除它们与旧镜头的关联，可在「准备资源」中重新分析。Excel 导入与模板下载不需要大模型 API Key。

脚本节点的「准备资源」仅按图片、视频、音频分组，分别使用青绿、蓝紫和暖金色卡片；文本与脚本在分镜编辑阶段处理，不再显示准备按钮。每项资源可上传同类型文件、从对应素材库选择或移除关联；人物/场景/道具等用途保留在分类下方。旧脚本的声音、旁白、配乐、音效会自动归到音频。新绑定类型不符时拒绝保存；旧错误关联会提示更换，不阻止无关编辑。

支持上传：JPG/PNG/WebP 图片 20 MB，MP4/WebM 视频 100 MB，MP3/WAV/M4A/OGG 音频 30 MB，UTF-8 TXT 1 MB，分镜 XLSX 5 MB。视频素材可播放、变速、以 0.04 秒步进和截取真实画面为图片。图片可创建连接好的视频生成节点；脚本可按镜头展开为视频工作流，提示词和对应图片资源随脚本同步。

视频生成链路仍以文本/脚本和图片为输入，暂不接受音频或视频素材作为视频生成参考。音频节点现可通过文本、参考音频或参考图片生成音效、配音和背景音乐。AI 扩图、抠图、重绘及批量生成分镜图片尚未接入。

## AI 图片生成

画布图片节点、图片资产库和脚本节点的图片资源卡均可点击「AI 生成图片」。图片或道具节点除了连接文本节点，也可以连接其他已绑定文件的图片或道具节点；这些图片会作为生图参考图，和画面描述一起提交。未连接参考图时仍按文字生成。每次最多 10 张，单张不超过 10 MB。脚本入口会预填资源名称、用途、整体风格和对应镜头内容；生成后先预览，再点击「使用这张图片」绑定当前节点或资源。图片库入口只入库，不自动添加画布节点。

在生成弹层的「模型设置」绑定图片模型。默认模型为 `doubao-seedream-5-0-pro-260628`，接口为火山方舟 `/api/v3/images/generations`，当前每次生成单张 2K 图片，默认开启水印。API Key 仅保存在服务端，页面只返回脱敏状态；留空保存不会清除已有密钥，也可用 `SEEDREAM_API_KEY` 环境变量提供回退配置。

图片生成通过 `POST /api/images/generations` 提交异步任务、`GET /api/images/generations/{id}` 查询状态，重复请求编号会复用任务以避免重复调用。生成图片会保留原始分辨率并保存到本地图片资产库（来源为 AI 生成），不依赖上游临时预览链接。关闭生成弹层后任务继续，可稍后进入素材库查看；服务重启时不会自动重发付费请求。

## AI 音频生成

画布「音频」节点提供文生音效、生成配音和生成背景音乐入口；音频素材库及画布的音频素材选择器也可直接打开生成面板。连接到音频节点的文本节点会作为描述，音频或图片节点可作为参考素材。生成后可试听、下载，或绑定回当前音频节点；结果会自动保存到音频素材库。输出支持 WAV、MP3、OGG Opus，可配置采样率、语速、音量、音调、字幕时间轴和音频水印。

在「模型管理 → 音频生成模型」添加或编辑火山引擎语音模型，默认模型 ID 为 `seed-audio-1.0`，地址为 `https://openspeech.bytedance.com/api/v3/tts/create`，鉴权使用 `X-Api-Key`。也可通过 `SEEDAUDIO_API_KEY` 环境变量给内置模型提供回退密钥。当前只接入此文档所述的非流式 Seed Audio 协议；其他服务商协议不能仅靠修改模型地址使用。

纯文本、文本加最多 3 条参考音频、文本加 1 张参考图片是支持的组合；音频与图片参考不可混用。音色 ID 占用一个音频参考位置，参考音频按上传顺序对应 `@音频1`、`@音频2`、`@音频3`。参考音频单条不超过 30 秒、10 MB，支持 WAV、MP3、OGG Opus；参考图片不超过 10 MB，支持 JPEG、PNG、WebP。现有素材库仍允许上传 M4A 与 OGG Vorbis，但它们不能直接用作 Seed Audio 参考，需先转换。模型单次输出上限为 120 秒，实际生成时长由提示词和服务商决定。

浏览器通过 `POST /api/audio/generations` 提交异步任务，再通过 `GET /api/audio/generations/{id}` 查询；重复请求编号不会重复调用上游。服务端把所选模型配置映射为 `model`、`text_prompt`、`references`、`audio_config` 和 `watermark`，将 Base64 或临时下载地址的结果保存成本地音频。音效、配音和背景音乐是建绘通的应用模式，通过 `text_prompt` 指令表达：背景音乐明确要求纯器乐、无人声；音效明确排除朗读和配乐；图片参考用于声音氛围，不用于口头描述画面。此方式属于模型提示约束，不保证输出绝对符合分类。指令与用户描述合计不能超过 3000 字，超出时提交前提示缩短，不会静默省略类型指令。关闭面板后任务继续；服务重启时未完成任务标记为中断，不会自动重发可能计费的请求。模拟契约与入库测试不调用真实付费接口：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_audio_generation.py" -v
```
