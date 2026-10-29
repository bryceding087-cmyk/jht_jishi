# 停止生成请求

首页与历史对话、独立与画布 Agent、画布图片／视频／音频节点、图片／音频弹窗共用发送与停止按钮。文本节点润色和旧脚本编辑器中的分镜、资源分析、提示词生成也使用同一停止协议。

## 按钮与交互

- 空闲时显示原发送／生成图标，请求开始后切换为方块停止图标；点击后显示「正在停止」，成功后恢复发送。
- 图标通过 200ms 缩放、旋转和透明度变化切换。系统开启减少动态效果时关闭过渡；按钮保留键盘焦点，停止处理中不重复提交。
- 运行中按 Enter 不触发停止或重复发送。首页图片与视频仍可通过「加入队列」提交下一项。
- 停止保留输入草稿、参考素材、已完成内容及历史记录。切换项目或节点后，旧请求不能覆盖新对象或解除新请求的忙碌状态。

## 停止协议

`POST /api/generation/stop` 接收 `kind`（`image`、`video`、`audio`、`agent`、`text`），以及原始 `request_id`、已知 `task_id` 或 `run_id`。可附带 `project_id`／`conversation_id`、`node_id` 校验目标范围。服务端按登录账号验证访问权限。

请求编号在发送前创建。即使原提交尚未到达服务端或还没返回任务编号，停止接口也会写入账号与请求类型隔离的持久停止记录；迟到的同编号提交返回 `409 generation_request_stopped`。再次发送必须使用新编号。文本请求在调用模型前及保存结果前检查停止记录。

返回状态包括 `pending_submission`、`stopped`、`stop_requested`、`already_finished`。客户端遇到 `already_finished` 时读取并保留真实完成结果，不能把已完成任务改成取消。

## 上游任务与结果

尚未提交的排队任务直接取消。已经发送给模型供应商的请求可能继续运行；目前没有接入供应商远端取消协议。停止会阻止应用继续执行后续步骤、绑定迟到结果或发出下一张图片请求，并返回 `provider_may_continue` 说明实际边界。

供应商已经受理的媒体任务继续保留真实状态和并发名额，直到收到终态或按现有未知结果流程处理。迟到的媒体和回执仍保存在历史中，用户可以查看与下载；停止标记不会被旧工作线程的保存覆盖，画布恢复也不会自动绑定已停止的结果。

Agent 的停止执行取消：清空待处理消息，保留已完成稿件和回执，并阻止旧响应恢复运行状态。旧工作线程结束前展示「正在停止」，期间不启动同一对话的新工作。原有暂停／继续入口仍保持可恢复语义。

## 验证

后端停止、账号隔离、排队取消、迟到结果与 Agent 兼容回归见 `tests/test_generation_stop.py`、`tests/test_agent_stop.py`、`tests/test_media_stop_results.py`、`tests/test_text_generation_stop.py`。浏览器验证见 `tests/request_button.browser.cjs`、`tests/agent_stop.browser.cjs`、`tests/home_submission.browser.cjs`、`tests/media_request_stop.browser.cjs`、`tests/text_requests_stop.browser.cjs`；媒体异步竞态见 `tests/media_request_races.test.mjs`、`tests/canvas_video_stop_races.test.mjs`。测试使用临时数据库与模拟模型，不调用真实收费服务。
