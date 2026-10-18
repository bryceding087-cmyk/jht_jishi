# 历史代码清理记录

审查日期：2026-10-18。

本次以实际启动入口、浏览器模块导入、页面模板、后端路由和测试引用为依据，清理能确认已经失去用途的依赖和样式。不会仅凭文件名含有 `legacy`、函数在文件内没有直接调用，或样式在首页不可见就删除代码。

## 已完成清理

| 范围 | 清理内容 | 核实依据 |
| --- | --- | --- |
| `package.json`、`package-lock.json` | 移除 `express` 及其间接依赖 | `server.js` 只导入 Node 内置模块并启动 Python Uvicorn；全项目没有 Express 导入或调用。HTTP 路由和静态资源由 FastAPI 提供。 |
| `public/asset-library.css` | 删除旧资产分类页签、文件夹侧栏、旧卡片正文/操作区、角色卡片、脚本表预览及其响应式样式 | 当前资产列表由 `al-history-*` 模板渲染；现用 HTML/JS 不再产生被删除的类名。保留素材选择器、预览、上传、表单和现用卡片样式。 |
| `public/platform-theme.css` | 删除对应旧资产布局的主题覆盖 | 与前述退役类名相同；混合选择器中现用的 `.al-search` 仍然保留。 |
| `public/image-generation.css` | 删除旧水印勾选样式和圆圈加载器及其动画 | 当前生成窗不再产生 `ig-checkbox`、`ig-spinner`，生成等待使用共享 `loadingMarkup`。 |
| `public/loading-state.css` | 删除旧项目列表加载器选择器 | 项目列表已使用可视区域媒体加载；保留项目读取失败提示和其他仍在使用的加载状态。 |
| `public/asset-library.js` | 删除无调用的 `sourceMatches`、外层 `date`、旧角色搜索变量和事件、空 `bindCharacters` 及其调用 | 角色搜索框已无模板，保留 `tagsOf`、元数据表单和仍被画布调用的兼容提示。 |
| `server.js` | 移除特定开发机器的 Codex 缓存 Python 回退路径 | 使用显式 `PYTHON`、项目虚拟环境或系统 Python；先校验 PostgreSQL 配置，再按配置启动项目本地实例。 |

四个样式文件合计删除 86 个无用的叶规则；保留的 262 个叶规则逐项比对后声明内容一致。少量混合选择器仅移除了已退役的选择器。

`npm start` 和 `npm run start:launcher` 均保留。Node 启动器没有第三方运行依赖；Python 运行依赖仍由 `requirements.txt` 管理。

## 有意保留的兼容代码

- 全部现有 `public/*.js` 和 `public/*.css` 都仍有实际页面或模块引用，未发现可直接删除的整个独立文件。多层样式覆盖不是整个文件失效的证据。
- `/api/models`、`/api/llm`、`/api/image-model` 与统一模型注册表并存，当前前端和旧配置读取仍在使用它们。不能将这些路由直接按“旧 API”删除。
- `/api/characters` 仍被前端数据刷新读取；角色创建和文本/脚本资产创建的退役响应是明确的兼容行为，删除路由会把可解释的业务错误变成 404。
- `media_library.py`、旧画布与 Agent 数据迁移、旧任务输入摘要的兼容处理用于已有持久化数据，仍有回归用例，不能与展示层的废弃样式一起清除。
- `asset-library.js` 的 `openCharacters`、`editCharacter`、`saveNode` 仍由 `app.js` 调用，当前用于给出退役功能提示。`tagsOf` 仍参与素材选择器搜索与素材元数据表单。
- Python 顶层函数的单次引用主要来自 FastAPI 装饰路由；路由函数没有显式 Python 调用不代表未使用。Pillow、openpyxl、mutagen、PyYAML 和 imageio-ffmpeg 均存在实际运行代码引用。

## 后续可单独整理的范围

`asset-library.js` 其余文件夹和元数据编辑函数需结合所有菜单入口与对外返回方法成组整理，不能通过全局删除名称来清理。

`styles.css` 等基础样式中还有旧页面选择器与现用选择器混合在同一规则内。进一步整理应逐个拆分选择器并对画布、素材选择器、生成弹窗和移动布局做页面回归，避免删除动态类名或现有 CSS 继承关系。

## 本次验证

- 使用离线 npm 重建锁文件，`npm ls --package-lock-only --all --offline` 确认依赖树为空。
- `node --check` 检查 `server.js`、`public/asset-library.js`、`public/image-generation.js`、`public/project-library.js` 均通过。
- 对四个 CSS 文件进行清理前后规则比对：保留声明不变、删除类名在现用 HTML/JS 中均无引用、花括号匹配。
- 本次清理未改动 Python 业务逻辑、路由、数据库、上传文件或测试数据；后端与 SDK、数据库适配的整体回归由对应变更统一执行。
