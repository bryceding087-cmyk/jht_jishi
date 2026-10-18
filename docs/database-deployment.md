# PostgreSQL 部署、迁移与备份

正式入口使用 PostgreSQL。`server.py` 会读取项目根目录 `.env` 并校验数据库类型，缺少连接配置或配置为 SQLite 时立即退出。SQLite 只用于旧数据导入及隔离测试，直接导入 `app` 不读取部署 `.env`。

`storage/` 使用 SQLAlchemy 管理数据库连接、事务和表结构。账号、会话、登录限制、迁移记录使用独立 SQL 表；业务记录的归属、创建者、上下文、任务、状态、媒体地址和时间有独立查询列与索引，画布等可变内容保存在 PostgreSQL JSONB 中。媒体文件继续保存在 `data/uploads/`。

## Windows 本地实例

先安装 `requirements.txt` 中的 Python 依赖；首次便携实例安装使用 PowerShell 7：

```powershell
pwsh -NoProfile -File .\scripts\setup_local_postgres.ps1
npm start
```

安装脚本从官方 EDB 下载 PostgreSQL 17.11 Windows x64 便携包并校验固定 SHA-256，运行目录为 `.runtime/postgresql/`，数据目录为 `data/postgresql/`。仅监听 `127.0.0.1:55432`，使用 SCRAM 随机口令，应用账号 `jianhuitong_app` 不具备超级用户、建库、建角色或复制权限。脚本不注册系统服务，不修改系统 PATH，不接管已有实例，不覆盖已有 `.env` 数据库配置。

`.env` 保存应用连接，`data/postgresql.local.json` 保存本地实例维护信息；两者和 `.runtime/`、`data/` 均被 Git 忽略。不要将文件内容贴到日志或公开文档中。

`npm start` 仅在有效连接与本地实例的主机、端口、库名、账号和目录完全匹配，且连接没有额外查询参数时启动该实例。查询参数可能覆盖 libpq 的主机或服务目标，因此这类连接一律交给外部数据库管理。日常启动通过 Python 直接调用 `pg_ctl`/`pg_isready`，不执行 `powershell.exe -File`，不需要改变脚本执行策略；所有 Windows 子进程隐藏窗口。重复启动不会重启已运行的 PostgreSQL。也可单独启动数据库或执行无副作用的配置检查：

```powershell
.\.venv\Scripts\python.exe server.py --start-local-database
node server.js --check
```

Node 启动器仅使用 Node 内置模块，不需要 Express。应用仍使用单个 Uvicorn worker；后台生成任务暂未改造成分布式队列。

## 使用外部 PostgreSQL

由数据库管理员创建应用库和有该库权限的应用账号，在 `.env` 配置 `JIANHUITONG_DATABASE_URL`，格式参照 `.env.example`。支持 `postgresql+psycopg://`、`postgresql://` 和 `postgres://`。然后运行 `npm start`，启动器只连接外部数据库，不启动、停止或安装它。

显式进程环境中的 `JIANHUITONG_DATABASE_URL` 优先，其次是显式 `DATABASE_URL`，再读取 `.env`；因此部署平台注入的外部数据库连接不会被本机 `.env` 覆盖。macOS/Linux 使用相同的 Python/Node 入口，PostgreSQL 实例由系统或托管服务提供。

## 从旧 SQLite 导入

导入前停止旧应用写入，并备份源 `data/jianhuitong.db` 和 `data/uploads/`。目标必须是空的 PostgreSQL 应用库；不要先启动新应用初始化预设账号，再尝试导入。

```powershell
.\.venv\Scripts\python.exe server.py --start-local-database
.\.venv\Scripts\python.exe scripts/run_migration.py --source data/jianhuitong.db
.\.venv\Scripts\python.exe scripts/run_migration.py --source data/jianhuitong.db --verify-only
npm start
```

外部 PostgreSQL 跳过第一行；macOS/Linux 将 Python 路径替换为 `.venv/bin/python`。`run_migration.py` 先读取部署环境与 `.env`，再调用 `migrate_sqlite.py`，无需将含口令的 DSN 写到命令参数中。

迁移以只读事务读取 SQLite，在 PostgreSQL 单一事务中导入并逐表核对内容。输出仅包含状态、摘要和数量，不打印连接字符串、账号口令、模型 Key 或业务正文。失败回滚目标写入，源 SQLite 和媒体文件保持不变；保留源库直到核对完成。重复执行只用于确认完全相同的导入快照，不能作为后续增量同步；应用开始产生新数据后不要再从旧库覆盖。

## 备份与恢复

项目本地实例可使用逻辑备份：

```powershell
pwsh -NoProfile -File .\scripts\backup_local_postgres.ps1
```

脚本使用 `pg_dump -Fc` 将当前应用库保存到 `data/backups/postgresql-时间戳.dump`，随后用 `pg_restore --list` 验证归档可读取。口令通过子进程环境传递，不出现在命令行。已有备份文件不会被覆盖。该脚本明确面向项目本地实例；外部数据库使用其运维平台或对应版本的 `pg_dump`，不要误把本地备份当作外部库备份。

数据库逻辑备份不包含媒体文件，需要同时备份 `data/uploads/`。若需两者一致，先暂停应用写入和生成，再分别保存 SQL 备份及媒体目录。备份包含模型配置和私人数据，应与部署凭据一起受控保存。

恢复时先创建新的空数据库并授权给应用账号，再用对应 PostgreSQL 的 `pg_restore --no-owner --no-acl` 恢复归档，将媒体放到新部署的 `data/uploads/`，最后配置新的连接并启动。先验证登录、项目数量、资产数量和媒体读取，再切换服务。不要复制运行中的 `data/postgresql/` 目录进行跨机覆盖；它不是跨版本的逻辑迁移格式。

## 开发与检查

`npm start -- --reload` 保留同样的 PostgreSQL 校验和本地实例启动流程，并启用 Python 文件重载。`node server.js --check` 只检查配置和本地/外部实例选择，不证明数据库网络可达，也不启动应用。

运行隔离测试前不要把正式 DSN 导出到测试进程。常规测试直接导入应用并使用临时 SQLite；PostgreSQL 专项测试应指向独立测试库，不能使用正式库。

## 本地切换记录（2026-10-18）

本次已将旧库的 1,593 条业务记录和 2 个账号迁入 PostgreSQL，并逐项核对通过，包括账号密码哈希。原始 SQLite 文件的 SHA-256 保持不变；迁移前备份保存在 `data/backups/pre-postgresql-1792267085296490800.sqlite3`，核对报告为 `outputs/runtime/postgres-migration-verified.json`。上述路径都属于忽略的本地数据，不随代码分发。

正式服务已通过 Node → Python → Uvicorn 启动，数据库为 PostgreSQL；`/login` 返回 200，`/api/health` 的 `storage` 为 `PostgreSQL`，匿名访问项目接口返回 401。此次启动日志为 `outputs/runtime/postgres-server-20261018-040141.out.log` 和 `outputs/runtime/postgres-server-20261018-040141.err.log`。这些是本次切换时的验证记录，后续数据增量应以 PostgreSQL 为准，不再向旧 SQLite 回写。
