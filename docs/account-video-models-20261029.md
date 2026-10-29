# 刘方雅个人视频模型更新（2026-10-29）

此包为指定账号增加两个官网视频模型，保留原集市视频模型。账号匹配使用完整用户名，不根据显示姓名匹配，也不会创建或重命名账号：

`market_d01ca182c05c58e4c9f62d7e69b13d58e703d3e81cb2d6097943315320da17d8`

| 配置名称 | 模型 ID | 提交接口 |
| --- | --- | --- |
| MiniMax H3（官网·个人） | `MiniMax-H3` | `https://api.minimax.cn/v2/video_generation` |
| Seedance 2.5（火山引擎·个人） | `doubao-seedance-2-5-260628` | `https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks` |

MiniMax 查询路径为 `/v2/query/video_generation/{task_id}`；火山引擎查询路径为 `/api/v3/contents/generations/tasks/{task_id}`。

## 使用

安装后，刘方雅刷新页面，在「模型管理 → 视频生成模型」可看到两个个人模型，点击「设为个人默认」即可切换。默认选择按账号保存，不修改全系统默认模型；已有视频节点保留原模型，可在节点的模型菜单另行选择。其他普通用户、同名用户及独立管理员的模型列表均不会显示这两份个人配置。

模型密钥仅由服务器使用，接口不返回明文密钥。独立官网请求使用个人配置中的密钥，原集市模型继续使用集市会话凭据；个人密钥只允许发送到其指定的视频接口，禁止跨站重定向。

MiniMax H3 支持 768P、2K，时长 4～15 秒。Seedance 2.5 支持 480p、720p、1080p，时长 4～30 秒。两者首尾帧模式均跟随首帧图片比例；请求中的 `ratio` 使用 `adaptive`。首尾帧只发送两个 `first_frame` / `last_frame` 图片项，全能参考使用 `reference_image`。

## 安装

版本：`account-video-update-20261029-1`。累计包含 10 月 29 日日志与并发更新（每账户 10、全系统 100 个视频任务），支持从此前七个版本以及 `admin-logs-update-20261029-1` 直接升级。没有依赖或数据库表变更。

本包的 `provision.py` 包含用户提供的官网密钥，属于专属部署包，请仅上传到目标服务器，不作为公共源码包分发。源码及文档不包含这两个真实密钥。

将 `.tar.gz` 和对应 `.sha256` 上传到服务器 `/tmp`。安装会重启应用，请在当前生成任务结束后执行：

```bash
cd /tmp
sha256sum -c jht-liufangya-video-update-20261029.tar.gz.sha256 && \
tar -xzf jht-liufangya-video-update-20261029.tar.gz && \
bash /tmp/jht-liufangya-video-update-20261029/apply.sh
```

安装器首先检查目标账号是否存在且启用；找不到精确用户名时停止，不修改代码或凭据。随后备份代码和个人配置，更新运行文件，在应用数据卷写入 `account-video-models.json`（权限 `0600`），重启并验证。已有其他账号专属配置、项目、素材、用户和模型配置保留。重复安装可安全执行。

目标服务器需要能够通过 HTTPS 访问 `api.minimax.cn`、`ark.cn-beijing.volces.com`，以及供应商返回的视频下载地址。两个官网的实际开通情况、额度和生成限制以官网账号为准。

## 回滚

```bash
bash /tmp/jht-liufangya-video-update-20261029/apply.sh rollback
```

代码备份位于 `/app/.deployment-patches/account-video-update-20261029-1/originals.json`。个人配置备份位于应用数据目录的 `.deployment-patches/account-video-update-20261029-1/private-models-backup.json`，同样包含敏感配置，仅服务器保留。若后续有人改过代码或个人配置，回滚会停止，避免覆盖新修改。

回滚保留业务数据。用户的个人默认选择记录也保留；个人模型不可用时会回到原有系统默认。代码补丁保存在当前容器，个人配置保存在数据卷；若使用旧镜像重新创建容器，需要重新安装对应代码更新。

## 验证范围

已通过官方文档核对 MiniMax V2 的模型名称、请求和查询协议，并通过火山引擎只读模型列表查到 Seedance 2.5 模型 ID。MiniMax 的只读查询能到达官网并返回任务不存在；这项检查不创建视频，也不代表验证过完整生成或剩余额度。

后端测试覆盖账号隔离、同名账号、个人默认保存、独立密钥与集市密钥隔离、禁止跨站转发、后台执行、结果解析、查询恢复、两张图片传递与首尾帧参数。Chrome 验证模型管理页面的切换、刷新保留和移动端布局。安装器验证覆盖已有配置保留、重复安装和回滚。

官方文档：[MiniMax H3 创建任务](https://platform.minimax.cn/docs/api-reference/video-generation-v2-create)、[查询任务](https://platform.minimax.cn/docs/api-reference/video-generation-v2-query)、[火山方舟创建视频任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)。

此包在本地构建，尚未连接生产服务器安装；未提交任何真实视频生成请求。
