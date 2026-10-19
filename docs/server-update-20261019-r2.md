# 建绘通累计更新补丁 R2（media-library-styles-2）

首页默认选择改为「图片生成」：首次进入、刷新首页、通过侧栏返回首页时均显示图片生成表单。Agent 仍可手动选择；打开已有对话或恢复正在进行的任务时，使用该对话原来的类型。

此包包含 [上一版累计补丁](server-update-20261019.md) 的全部功能和 152 种风格资源，可直接安装到 `video-reliability-1`、`market-task-return-1` 或 `media-library-styles-1`。此前的集市登录与 HTTPS 代理修复须已安装。无需先安装上一份累计包，已安装上一份的服务器也能直接升级。

## 上传和安装

将下面两个文件上传至服务器 `/tmp`：

- `jht-media-library-styles-20261019-r2.tar.gz`
- `jht-media-library-styles-20261019-r2.tar.gz.sha256`

安装会重启 `jht-app`。等当前生成任务结束后，逐条执行：

```bash
cd /tmp
sha256sum -c jht-media-library-styles-20261019-r2.tar.gz.sha256
tar -xzf jht-media-library-styles-20261019-r2.tar.gz
cd /tmp/jht-media-library-styles-20261019-r2
bash apply.sh
```

脚本先校验版本与文件哈希，备份后更新并重启应用，随后检查服务健康、集市入口、静态资源及风格文件。更新成功后重新进入建绘通首页，必要时 `Ctrl+F5` 刷新，应默认选中「图片生成」。

包内不含本地配置、密钥、用户数据库或素材，不改动服务器环境配置及数据库表结构。服务器模型网关继续默认使用 `http://192.168.241.202:4091`，显式配置优先；无需导入新镜像或安装新依赖。

## 回滚

```bash
cd /tmp/jht-media-library-styles-20261019-r2
bash apply.sh rollback
```

回滚恢复安装前的代码版本，保留业务数据。备份位于容器 `/app/.deployment-patches/media-library-styles-2/originals.json`；未知版本或后续文件修改会触发保护并停止。

补丁保存在当前容器中，重启同一容器仍有效。删除容器并以旧镜像重建会恢复旧代码，需要重新安装补丁或用最新源码构建镜像。包内 `UPDATE-MANIFEST.json` 记录源代码版本和文件哈希。

## 验证

已使用隔离数据和真实 Chrome 检查默认选择、图片表单、切换 Agent、返回首页、已有视频对话与桌面／窄屏布局。安装、重复安装、回滚及版本保护在临时目录验证；目标服务器启动由安装脚本现场检查。
