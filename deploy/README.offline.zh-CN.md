# 建绘通：麒麟 V10 离线部署

目标服务器已确认：Kylin Linux Advanced Server V10，x86_64，内核 `4.19.90-52.22.v2207.ky10.x86_64`，根分区可用约 431 GB，现有 Podman 为 0.10.1。

## 包内内容和验证边界

- `images/jht-jishi.tar.gz`：可供 Docker load 导入的 Linux amd64 应用镜像，标签 `jht-jishi:20261018`。包含 Python 3.11、项目代码、Linux wheel 依赖、FFmpeg、Noto CJK 中文字体。
- `images/postgres.tar.gz`：PostgreSQL 17 的 Linux amd64 镜像，标签 `postgres:17-bookworm`。
- `engine/docker-28.5.2.tgz`：Docker Engine 28.5.2 的 Linux x86_64 静态二进制离线包。
- `config/app.env`、`config/postgres.env`：本次部署配置。已携带提供的音频 Key，并生成独立随机数据库密码。密钥不在镜像内；请将整个部署目录作为包含凭据的私有文件保管。
- 安装、启动、管理员密码设置脚本及 SHA-256 清单。

这不是单纯源码包，运行所需镜像和引擎文件已随包提供，服务器安装和启动不需要下载软件。模型生成仍依赖对应内网/外部服务，不是本地离线大模型推理。

本机没有 Linux 容器引擎。应用镜像采用 Docker-save 格式，将经过摘要核对的上游 Python 镜像层与 Linux wheels、应用及字体层组装成归档；已检查镜像结构、各层摘要、amd64 ELF 架构、依赖元数据和动态库名称依赖。没有在本机运行过 `docker load` 或 Linux 容器，因此不能将归档检查视为服务器启动验证。`start-offline.sh` 会先在服务器运行实际原生依赖、FFmpeg/libass、中文字体测试，再启动业务，并核对数据库健康。

上游镜像来自 Docker Hub 官方 `library/python` 和 `library/postgres`，通过 DaoCloud 镜像代理下载；其 manifest 和 layer 摘要保存在 `IMAGE-MANIFEST.json`。Docker 引擎来自阿里云 docker-ce 镜像站，字体及 wheels 来自清华镜像源。`SHA256SUMS` 用于核对本次打包后的传输完整性，不是发布方数字签名。

## 1. 上传前先看清服务器

截图中的堡垒机页面是 JumpServer/Luna，资产会话显示 `192.168.168.116`；这只是当时选中的资产，最终请以要部署的资产为准。截图末尾 `Connection websocket closed` 表示会话已断开，先重新连接。

在该资产会话的“文件管理”中，将整包上传到目标服务器，例如 `/root/jht-upload/`。文件管理与终端必须是同一资产。若堡垒机限制单文件大小，可在上传端分片，服务器端按顺序合并后校验原整包 SHA-256；不要用终端粘贴二进制内容。

## 2. 校验和解压

以下命令逐条执行，不要把多条命令连在同一行粘贴。

```bash
cd /root/jht-upload
sha256sum -c jht-jishi-offline-linux-amd64-20261018.tar.gz.sha256
mkdir -p /opt/jht-jishi-release
tar -xzf jht-jishi-offline-linux-amd64-20261018.tar.gz -C /opt/jht-jishi-release
cd /opt/jht-jishi-release/jht-jishi-offline
sha256sum -c SHA256SUMS
```

目录应当首次使用。不要解压覆盖已有部署；升级时使用新的发布目录。若上传到了别处，替换第一行路径。所有校验通过后再继续。

`SHA256SUMS` 校验镜像、引擎和脚本等固定文件；可编辑的 `config/*.env` 不在该内部清单内，其初次传输完整性由外层整包 SHA-256 覆盖。因此可以按实际环境修改配置后再启动。

## 3. 离线安装独立 Docker 引擎

已有 Podman 0.10.1 太旧，本方案不依赖它，也不需要卸载它。脚本安装独立 `jht-docker.service`：

```bash
bash install-docker-offline.sh
systemctl status jht-docker --no-pager
jht-docker info
```

Docker 二进制位于 `/opt/jht-docker/bin/`，容器数据位于 `/var/lib/jht-docker`，管理 socket 是 `/run/jht-docker.sock`。命令 `jht-docker` 专门连接这个引擎，不能用系统默认 `docker` 命令混淆实例。脚本不会覆盖已有 `/etc/docker/daemon.json`，不配置网桥，不修改 iptables，不停用 SELinux，不卸载或清理原 Podman。

如果服务启动失败，先执行下面命令收集日志，不要继续启动应用，也不要关闭 SELinux或更改系统安全策略来绕过错误：

```bash
journalctl -u jht-docker -n 100 --no-pager
```

内核版本与磁盘空间满足本方案的初步要求，但 overlay2、cgroup、SELinux 和主机策略仍以这台服务器的实际运行结果为准。

## 4. 导入并启动

```bash
bash start-offline.sh
```

脚本会核对文件、导入两个本地镜像，执行应用运行环境检查，创建持久卷并启动容器。所有 `docker run` 都使用 `--pull=never`，不会尝试联网拉取镜像。

- 容器 `jht-postgres` 只监听服务器 `127.0.0.1:55432`，不对局域网发布数据库。
- 容器 `jht-app` 监听服务器 `0.0.0.0:8000`。
- 数据库持久卷：`jht-postgres-data`；上传与生成素材持久卷：`jht-app-data`。
- 容器使用宿主网络以访问已有内网网关；数据库和应用的重启策略为 `unless-stopped`，独立 Docker 服务随开机启动。
- 应用使用普通 UID 10001 和单个 Uvicorn worker；数据库应用账号无超级用户、建库或建角色权限。

健康检查通过后：

```bash
jht-docker ps
curl -fsS http://127.0.0.1:8000/api/health
bash reset-admin.sh
```

最后一个命令会交互设置 `admin` 的新密码并注销之前的管理员会话；输入不显示，也不写到命令参数中。包中没有历史数据库和作品，首次启动是新实例。原有 `dingyufu` 预设账号仍由应用初始化；上线前应按单位账号管理要求核对成员账号。

如果端口 8000 或 55432 已被占用，脚本会停止，不会终止占用端口的进程。容器已存在时也会停止，不会覆盖或删除已有数据。检查错误后，可用 `jht-docker start jht-postgres jht-app` 启动已经创建的容器；不要反复删除数据库卷。

## 5. 发布网页和连接 AI 集市

服务器自身的地址不是堡垒机 `192.168.240.3`。堡垒机用于远程运维和传文件，并不自动发布建绘通网页。

先在网络策略允许的客户端访问 `http://<目标服务器IP>:8000/login`。正式提供给 AI 集市的 URL 为通过批准的内网域名/反向代理发布的 `https://<建绘通域名>/market`。外层集市页面若使用 HTTPS，内嵌应用也应发布为 HTTPS，不能直接嵌入 HTTP 页面。宿主机防火墙、路由、aTrust/Web 应用入口和反向代理的配置需要按实际网络方案完成；脚本不会自动开放主机端口。

`config/app.env` 已配置模型网关：`http://192.168.241.202:4091`。文本、图片、视频提交与查询路径位于 `/v1`。集市网页来源 `JIANHUITONG_MARKET_ORIGIN` 当前仍为 `https://aimarket.hncig.cn:4090`；它必须匹配浏览器实际打开的集市网页来源。若集市网页也迁到内网，修改该项为实际 origin。跨站 Cookie、代理 HTTPS 与信任代理地址也必须按实际发布方式配置，不能仅凭堡垒机可登录判断网页接入已完成。

音频目前仍直连 `https://openspeech.bytedance.com/api/v3/tts/create`；无外网服务器需要音频内网代理或出口，否则音频生成无法完成。上游返回的图片/视频素材地址也必须能从服务器访问；当前下载器会拒绝不符合其协议和地址校验的地址，若网关返回内网素材链接，需要另行按实际素材域名适配，不能通过关闭全部地址校验解决。

## 日常查看、停止与数据备份

```bash
jht-docker logs --tail 100 jht-app
jht-docker logs --tail 100 jht-postgres
jht-docker stop jht-app jht-postgres
jht-docker start jht-postgres jht-app
```

停止容器不会删除两个持久卷。不要运行 `volume rm`、`system prune --volumes` 或直接删除 `/var/lib/jht-docker`。

数据库逻辑备份示例（在服务器上选一个权限受控且空间足够的目录）：

```bash
umask 077
jht-docker exec jht-postgres pg_dump -U postgres -p 55432 -Fc jianhuitong > jianhuitong.backup
```

还需单独备份素材卷，恢复前停止应用写入。配置文件内的数据库和音频密钥也要在受控位置备份。应用版本升级只替换应用容器与镜像，不删除持久卷；具体升级和回滚应基于届时的数据库迁移要求。

## 源码中增加的构建入口

项目根目录提供 `Dockerfile` 与 `.dockerignore`，可在有外网且具备 Docker 的 Linux amd64 构建机上执行标准构建：

```bash
docker build --platform linux/amd64 -t jht-jishi:20261018 .
docker save jht-jishi:20261018 | gzip > jht-jishi.tar.gz
```

标准 Dockerfile 路径通过 Debian 安装 FFmpeg 和 Noto 字体，与本次跨平台镜像组装路径的实现方式不同；两者均必须通过运行环境和业务健康检查。不要将当前 Windows `.venv` 复制进 Linux 镜像。
