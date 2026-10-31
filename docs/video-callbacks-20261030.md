# 视频提交超时与官方回调更新（2026-10-30）

视频提交统一等待 120 秒，状态查询仍使用原有超时与退避重试。提交超时、连接中断或响应无法解析，且尚未收到供应商任务 ID 时，任务保留为“提交结果待确认”，不自动再次 POST。已收到任务 ID 的任务继续查询原任务。

火山方舟 Ark 与 MiniMax 官方 V2 接口支持任务专属回调。回调记录在提交前写入数据库，只保存随机凭据的哈希、原模型配置和密钥指纹，不保存新的 API Key 副本。通知先持久保存并快速响应，再由后台用原账号凭据 GET 验证真实结果；不会直接采信通知里的状态或视频下载地址。回调可早于提交响应到达，重复回调不会重复下载或创建付费任务。已收到的通知在重启后继续处理，官方个人模型不依赖原浏览器集市会话保持在线。

MiniMax 的 `challenge` 请求由同一入口快速原样返回，处理路径不访问模型、不下载视频。官方推送使用自己的任务结构：Ark 为 `id`，MiniMax 为 `task.id`。

AI Market 代理和自定义协议没有经过回调协议确认，因此本版对它们应用 120 秒提交等待与查询恢复，不擅自添加官方 `callback_url`。不根据时间、提示词相似度将任务列表里的结果自动绑定给用户。

## 配置回调地址

本版已将用户确认的公网入口设为代码默认值，未设置环境变量的旧容器安装补丁并重启后即可使用。`.env.example` 同步配置为：

```dotenv
JIANHUITONG_VIDEO_CALLBACK_BASE_URL=https://aimarket.hncig.cn:4193
```

`https://aimarket.hncig.cn:4193/market` 是页面入口，`/market` 不放入回调基础地址。`http://192.168.168.117:8000` 是内网后端，只用作公网代理的转发目标，不作为供应商的回调地址。如果以后更换公网地址，可通过上述环境变量覆盖默认值；如果应用在反向代理子路径下，可包含对应前缀。

2026-10-30 从开发机检查公网 `/market` 返回 200；未部署补丁时，两个回调路径的测试 POST 均返回 401“请先登录后再操作”。本次代码已经对准确匹配的回调 POST 使用任务凭据校验。生产代理仍需将请求送达新代码，且**火山和 MiniMax 服务器必须能访问该 HTTPS 地址及 4193 端口**；开发机可访问不代表供应商侧已验证。

应用会自动生成以下路径，部署人员不需要手工创建每个任务的 token：

```text
POST /api/video-callbacks/ark/{本地任务ID}?token={随机任务凭据}
POST /api/video-callbacks/minimax/{本地任务ID}?token={随机任务凭据}
```

这两个 POST 入口使用任务凭据，不依赖浏览器登录或 CSRF Origin；其他 API 保持原认证。公网代理应将 `/api/video-callbacks/…` 转发到 `http://192.168.168.117:8000/api/video-callbacks/…`，完整保留 POST 方法、请求体和查询参数，不插入登录页面；回调日志应省略查询字符串，避免记录短期任务凭据。

未设置变量时使用上述公网地址；**显式设置为空值**时关闭回调，官方接口继续采用提交加轮询。已有显式空值不会被补丁覆盖。HTTPS 地址格式错误时启动会明确报错。

使用默认地址无需调整容器环境配置。需要覆盖时，普通启动在 `.env` 中设置后重启；Docker 部署则修改实际使用的 `config/app.env` 或 Compose 环境配置并重新创建容器，单纯 `docker restart` 不会重新读取 `--env-file`。重新创建容器必须同时使用包含本次代码的镜像，或在重建后重新安装本次补丁。已有个人模型文件和生成记录保留。

## 安装更新包

版本 `video-callback-update-20261030-2`，替代此前未配置默认地址的第一版。支持当前源码基线、`account-video-update-20261029-1` 个人视频模型更新和 `video-callback-update-20261030-1` 回调更新后的对应文件，无需先安装第一版。安装前对所有目标文件校验版本，出现未知改动会停止。包不包含真实模型密钥，不修改已有环境文件或数据库结构。

上传压缩包及校验文件到 `/tmp`，在当前生成任务结束后执行：

```bash
cd /tmp
sha256sum -c jht-video-callback-update-20261030-v2.tar.gz.sha256
tar -xzf jht-video-callback-update-20261030-v2.tar.gz
bash /tmp/jht-video-callback-update-20261030-v2/apply.sh
```

回滚：

```bash
bash /tmp/jht-video-callback-update-20261030-v2/apply.sh rollback
```

安装脚本检查源文件、应用健康状态和两个本机回调入口，并打印实际生效的回调基础地址。用无效任务凭据探测回调入口时应返回 404“回调凭据无效或已过期”，而不是 401 登录提示；此检查不会创建视频任务。确认公网代理同样返回该结果后，再通过真实视频任务验证供应商回调。404 仅证明请求到达回调处理器，不代表完整回调成功。

本补丁不会修改公网代理或防火墙配置。页面文件版本号已更新，部署后刷新页面。

## 验证及限制

- 本地测试覆盖 120 秒提交参数、无 ID 超时不重发、回调早于提交响应、重复与乱序通知、原账号隔离、凭据变更、MiniMax challenge、网络异常后的持久重试、重启及集市会话退出后的恢复。
- 回调需要在**创建该任务时**传入。此前未配置回调的历史超时任务不会补发通知，可在供应商任务列表中人工核对。
- 任务被供应商明确拒绝或根本没有成功提交时，不会产生可找回的视频。
- 通知丢失且没有任务 ID 时仍保留待确认状态，不保证永久恢复。保留既有“30 分钟无有效进展释放名额”的队列策略；删除、取消及已超时的任务不会被迟到回调恢复到运行状态。
- 回调地址配置、DNS、TLS、供应商可达性需要部署后的真实验证；本地测试不代表生产链路已验证。

官方文档：[火山创建任务](https://www.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh)、[火山任务列表](https://www.volcengine.com/docs/ark/list-video-generation-tasks-api?lang=zh)、[MiniMax 创建任务](https://platform.minimax.cn/docs/api-reference/video-generation-v2-create)、[MiniMax 查询任务](https://platform.minimax.cn/docs/api-reference/video-generation-v2-query)。
