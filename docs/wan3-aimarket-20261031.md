# AI 集市通义万相 Wan 3.0 视频模型

版本：`wan3-aimarket-20261031-1`。

所有现有和新建用户共用一条模型配置，名称为「通义万相 Wan 3.0（AI 集市）」、模型 ID 为 `wan3.0-video`。首次启动将它写入共享模型表，不改变已有默认模型。后续启动保留管理员的编辑和删除决定。集市用户使用自己的 SDK 会话 Key，配置不包含共享 Key。

服务器网关为 `http://192.168.241.202:4091`；本地通过 `npm start` 启动沿用 `https://aimarket.hncig.cn:4090`。显式 `JIANHUITONG_MARKET_MODEL_ORIGIN` 配置优先。浏览器握手 origin 不变。

提交：`POST /v1/services/aigc/video-generation/video-synthesis`，携带 `Authorization: Bearer <当前用户Key>`、`X-DashScope-Async: enable` 和 JSON 内容类型。

```json
{
  "model": "wan3.0-video",
  "input": {"prompt": "蓝天白云，镜头缓慢移动"},
  "parameters": {"resolution": "480P", "duration": 2, "ratio": "16:9"}
}
```

查询：`GET /v1/tasks/{task_id}`。读取 `output.task_id`、`output.task_status` 和成功结果中的 `output.video_url`，沿用现有队列、用户隔离和本地视频保存流程。请求错误支持 DashScope 字符串错误码。

当前仅开放用户提供的请求示例所确认的文生视频、480P、2 秒、16:9。未假设其他分辨率、时长、画幅或参考图能力。需取得该网关完整模型契约后再扩展。供应商权限、积分和网关连通性仍以实际环境为准；本次验证使用模拟供应商，不调用收费生成。

## 服务器安装

更新包累计包含本地已有的视频回调和文件恢复更新，可用于原源码基线、账号模型更新、回调 v1/v2 和视频文件恢复版。所有文件在写入前统一校验，未知源码版本会停止；不覆盖 `.env`、密钥、用户素材或账号数据。

等待现有生成任务结束后，将压缩包和外部校验文件上传到服务器 `/tmp`，执行：

```bash
cd /tmp
sha256sum -c jht-wan3-aimarket-20261031.tar.gz.sha256
tar -xzf jht-wan3-aimarket-20261031.tar.gz
bash /tmp/jht-wan3-aimarket-20261031/apply.sh
```

安装器更新并重启 `jht-app`，确认运行文件、服务健康、数据库中的共享模型和服务器内部网关。刷新集市应用重新握手，在视频模型列表中选择新模型。

回滚源码：

```bash
bash /tmp/jht-wan3-aimarket-20261031/apply.sh rollback
```

回滚保留业务数据，新增模型记录也会保留；旧代码不支持此协议，请由管理员删除这条配置或待重新升级后使用。重建容器时应将本次源码纳入镜像，或重新安装更新包。

## SDK 积分价格检查

核查文件为 `public/vendor/aimarket.js`。SDK 唯一公开方法是 `aimarket.connect()`；成功后只返回 `{user, agent, apiKey}`。用户字段包含身份和组织信息，应用字段为 ID 和名称。没有模型目录、计价、余额、预估积分或实际扣费接口，也没有这类返回字段。

因此无法通过目前这份 SDK 获取不同模型的积分消耗价格。会话 Key 能调用生成接口，不代表 SDK 提供计价能力。需要集市提供模型计价/积分规则接口及鉴权契约，实际扣费则还需要调用用量或账单接口。此结论仅针对项目现有 SDK，不等于断言平台没有内部计费接口。

## 验证

`test_provider_adapters.py` 覆盖请求体、异步头、查询地址、状态和错误、队列执行与视频本地保存、共享可见性及重复安装保留编辑/删除。`test_market_sdk_login.py` 覆盖两个集市账号各自使用自己的 Key。网关配置测试覆盖本地公网与服务器内网地址切换。更新包制作过程验证支持版本的安装、重复安装、回滚以及未知改动拒绝。
