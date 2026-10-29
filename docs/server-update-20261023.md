# 建绘通累计更新包（2026-10-23）

版本：`agent-playback-update-20261023-1`。使用最新本地工作区代码（包含尚未提交的修改），沿用之前的 `apply.sh` 更新现有 `jht-app` 容器的流程。

支持从 `video-reliability-1`、`market-task-return-1`、`media-library-styles-1`、`media-library-styles-2` 和 `media-update-20261020-1` 直接升级。集市登录及 HTTPS 代理修复须已安装，无需逐个安装中间版本。

## 包含的更新

- 修复视频播放时任务轮询重建播放器、画布同步重建全部节点的问题，保留未变化的节点、播放位置和播放状态。
- 包含最新 Agent 创作流程、故事板与画布交互、批量提示词、素材直接引用、停止请求及对话进度展示等已完成修改。
- 首页默认 Agent；项目区分对话和画布；生成的图片和视频预览、短标题及深色界面等累计修复。
- 图片任务不限制应用层并发，视频任务并发上限为 5；单任务持续 30 分钟无有效进展后失败并释放队列。失败记录保留在历史中。
- 包含此前的集市任务恢复、素材管理、视频缓存及 152 种视觉风格资源。

更新包不含 `.env`、真实密钥、数据库、用户素材或本地依赖，不覆盖服务器现有模型配置。新增模型的本地配置和本地任务删除操作属于业务数据，不会随代码包复制到服务器。依赖清单与旧版相同，无需导入新镜像或安装新依赖。

## 上传和安装

上传这两个文件至服务器 `/tmp`：

- `jht-agent-playback-update-20261023.tar.gz`
- `jht-agent-playback-update-20261023.tar.gz.sha256`

安装会重启 `jht-app`，应在当前生成任务结束后执行：

```bash
cd /tmp
sha256sum -c jht-agent-playback-update-20261023.tar.gz.sha256
tar -xzf jht-agent-playback-update-20261023.tar.gz
cd /tmp/jht-agent-playback-update-20261023
bash apply.sh
```

校验失败或版本不匹配时停止后续步骤。脚本使用已有的 `jht-docker` 命令，核对整个补丁和服务器旧代码，备份、更新并重启容器，然后检查服务健康、集市入口、运行文件及静态资源。重复安装会报告 `ALREADY APPLIED`。未知代码版本会被拒绝，避免覆盖服务器上的其他修改。

成功后重新打开建绘通，必要时使用 `Ctrl+F5` 刷新。

## 回滚

```bash
cd /tmp/jht-agent-playback-update-20261023
bash apply.sh rollback
```

备份位于 `/app/.deployment-patches/agent-playback-update-20261023-1/originals.json`。回滚恢复安装前的代码并保留业务数据；新建资源目录可能保留为空目录。若安装后有其他代码修改，回滚会停止以避免覆盖。重启检查失败时可先查看 `jht-docker logs --tail 80 jht-app`。

补丁保存在当前容器中。重启同一容器仍有效，删除容器并用旧镜像重建后需要重新安装补丁，或用最新源码构建镜像。

## 验证与源码包

打包时验证五个支持基线的安装、重复安装、回滚、重新安装、版本冲突和损坏内容拒绝，以及配置和素材保留；校验新增 Agent 资源目录的创建。包内 `UPDATE-MANIFEST.json` 记录工作区快照、基准提交及文件 SHA-256。

另提供 `jht-source-20261023.zip` 和 SHA-256 校验文件，用于最新源码留档或重新构建，不是服务器 `apply.sh` 安装包。源码包保留测试、部署说明及 `.env.example`，排除真实环境配置、数据、素材、依赖、临时工作文件和 Git 历史。

未连接目标服务器实施部署，服务器实际健康状态由安装脚本现场核验。
