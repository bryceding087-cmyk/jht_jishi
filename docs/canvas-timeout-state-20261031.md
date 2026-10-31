# 画布超时任务误显示已停止

版本：`canvas-timeout-state-20261031-1`。

任务队列在连续 30 分钟无有效进展时，将任务设为 `failed`，设置 `queue_timed_out_at` 并释放名额。为防止迟到回写，同时设置 `local_stop_requested`、`stop_requested`。原画布前端把所有带停止标记的任务统一转换为 `cancelled`，导致没有手动停止的超时任务也显示「已停止」。

修复后，自动超时保留失败状态、显示「任务超时」并保留原错误。真实停止仍显示「已停止」。同一任务迟到的生成中响应不能覆盖已超时状态，新任务使用新 ID 正常执行。下载文件超时与队列超时是不同状态；此次只修改画布状态解释，不改变后台停止保护、队列限制和付费调用策略。

回归覆盖：自动超时停止标记、历史重新加载、迟到轮询、后续新任务，以及已有的手动停止和请求竞态。18 项任务队列测试通过。

## 服务器安装

累计包含彩色三角形、轮询闪烁、Wan 3.0、视频回调与文件恢复更新。待生成任务结束，将更新包及校验文件上传 `/tmp`：

```bash
cd /tmp
sha256sum -c jht-canvas-timeout-state-20261031.tar.gz.sha256
tar -xzf jht-canvas-timeout-state-20261031.tar.gz
bash /tmp/jht-canvas-timeout-state-20261031/apply.sh
```

重新载入集市中的建绘通页面，入口版本号已变更。安装器预先校验全部文件、备份并重启应用。服务器仍使用内部模型网关。

回滚源码：`bash /tmp/jht-canvas-timeout-state-20261031/apply.sh rollback`。业务数据不回滚；如回滚到不支持 Wan 的旧代码，勿选择保留的 Wan 模型配置。
