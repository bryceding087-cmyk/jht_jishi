# 建绘通：通过 JumpServer 上传程序

准备日期：2026-10-18。来源目录：`D:\coding\projects\jainhuitong`。

本次按“先上传程序，历史数据后续迁移”准备。源码包名为 `jainhuitong-source-20261018.zip`，解压后的顶层目录为 `jainhuitong/`。这里沿用你的本地文件夹拼写；程序配置变量仍使用项目原有的 `JIANHUITONG_` 前缀。

## 包里有什么

包含 Python 源码、`storage/` 数据访问代码、`public/` 网页及静态资源、`agents/`、`resources/`、脚本、测试、文档、依赖清单、锁文件和 `.env.example`。`storage/` 是代码，已完整保留。

未包含本地 `data/`（数据库与媒体）、真实 `.env`、`.runtime/`、`.venv/`、`node_modules/`、`outputs/`、缓存及运行日志。源码包不包含已安装的 Python 依赖，因此不能作为离线运行环境直接启动。原项目文件未作修改。

## 先完成上传

1. 保持 aTrust 的“安全接入中”状态，打开 JumpServer 堡垒机。
2. 在资产列表选中实际要部署的服务器，例如 `192.168.168.113` 或 `192.168.168.116`，核对资产 IP 和登录账号。
3. 在该资产的会话或文件管理页面打开“文件管理 / 文件传输 / SFTP”。实际入口名称依 JumpServer 版本而定。如果入口不可用，需要管理员确认该资产的文件上传权限。
4. 选择自己有写入权限的目录，将 `jainhuitong-source-20261018.zip` 上传。记下文件管理器显示的服务器端完整路径；部分环境默认上传到临时目录，并非主目录。
5. 在同一台目标服务器上核对文件大小，再解压到一个新建的项目目录。上传和解压完成即达到本次“先上传程序”的目标。

## 如果服务器是 Linux

以下示例假设 ZIP 已上传到当前用户主目录；如果实际路径不同，替换命令中的 `~/jainhuitong-source-20261018.zip`。

```bash
# 查看服务器端文件
ls -lh ~/jainhuitong-source-20261018.zip

# 使用新的部署目录；如同名目录已有项目，另选一个新目录
mkdir -p ~/deployments/jainhuitong-20261018
unzip -n ~/jainhuitong-source-20261018.zip -d ~/deployments/jainhuitong-20261018

# 确认源码和配置模板已经就位
cd ~/deployments/jainhuitong-20261018/jainhuitong
ls -l app.py server.py server.js requirements.txt .env.example
ls -ld public storage agents resources
```

`unzip -n` 保留同路径已有文件；请优先解压到全新的目录，避免旧代码与新代码混在一起。如果未安装 `unzip`，且服务器已有 Python 3，可用以下命令代替解压步骤，同样只用于新的目录：

```bash
python3 -m zipfile -e ~/jainhuitong-source-20261018.zip ~/deployments/jainhuitong-20261018
```

## 后续要启动时再做

已经核对项目入口：Python/FastAPI 提供 API 和前端静态文件，前端无需单独构建。Node.js 启动器负责调用 Python；正式运行必须配置 PostgreSQL。推荐按现有项目文档使用 Python 3.11+、Node.js 18+ 及可连接的 PostgreSQL。

先查看服务器已有的环境：

```bash
python3 --version
node --version
npm --version
```

确认服务器能够从互联网或批准的内部镜像安装 Python 依赖后，在上述项目目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci

# 如果已有 .env，保留现有配置
test -f .env || cp .env.example .env
```

由数据库管理员提供一个用于本次新部署的 PostgreSQL 数据库及应用账号。编辑服务器上的 `.env`，将 `JIANHUITONG_DATABASE_URL` 改为服务器实际可访问的连接。例如：

```dotenv
JIANHUITONG_DATABASE_URL=postgresql+psycopg://APP_USER:URL_ENCODED_PASSWORD@DB_HOST:5432/APP_DATABASE
HOST=127.0.0.1
PORT=8000
```

上面的账号、密码、主机和库名都是占位符，必须替换。密码里的特殊字符需按 URL 编码。端口使用实际数据库端口；本地示例的 `55432` 不代表服务器的数据库端口。Linux 不使用项目的 Windows 便携数据库或 `.ps1` 安装脚本。

```bash
# 只校验连接配置格式，不连接数据库、不启动应用
node server.js --check

# 配置和数据库就绪后，前台启动以查看日志
npm start
```

另开同一台服务器的会话查看：

```bash
curl -fsS http://127.0.0.1:8000/api/health
```

`--check` 成功仅表示配置检查通过；应用成功启动且健康接口返回正常状态、`storage` 为 `PostgreSQL` 后，才说明运行链路已验证。项目当前要求单 worker，保留现有启动入口。`npm start` 是前台启动，长期运行还需要按服务器规范配置进程管理。

这里保留默认的服务器本机监听地址。电脑通过 aTrust 登录堡垒机，并不自动获得应用网页访问地址；需要按单位的内网发布方式配置域名、反向代理或 aTrust Web 应用入口。

## 如果服务器是 Windows

同一源码包也可使用。通过目标资产支持的文件传输入口上传后，解压到新的部署文件夹。后续按照包内 `README.md` 的 Windows 章节准备 Python、Node.js 和 PostgreSQL。项目自带 `scripts/setup_local_postgres.ps1` 面向 Windows 本地便携实例，是否用于服务器应由该服务器的部署方式决定；已有 PostgreSQL 时直接配置 `.env`。

## 后续迁移已有项目和素材

这次没有导出或上传本机数据库、账号记录、模型配置或 `data/uploads/`。之后完整迁移时，需要 PostgreSQL 的逻辑备份与对应媒体目录，详见包内 `docs/database-deployment.md`。

如果先启动了新部署，新数据库会产生初始化账号或其他记录。后续恢复完整备份时应恢复到另一个新的空数据库，验证后再切换连接，避免把备份混入已经初始化或正在使用的数据库。请保留原电脑的项目和数据直到迁移完成。

## 本次检查范围

源码包通过 ZIP 完整性检查，并逐文件核对 SHA-256；必要入口文件、静态资源与 `storage/` 代码均已检查包含，本地数据与运行环境已检查排除。包内 `PACKAGING_MANIFEST.json` 保存文件清单与哈希。

本次完成的是本地上传包准备。尚未向服务器传输文件，未修改服务器、安装服务器依赖或验证服务器上的启动结果。
