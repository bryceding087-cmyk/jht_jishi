# 建绘通视频创作 Agent 文档入口

当前定义统一保存在 [agents/video-creation](../../agents/video-creation/README.md)，本文件仅保留导航，不作为主控指令加载。

- [Agent 总览与默认流程](../../agents/video-creation/README.md)
- [主控 Agent 指令](../../agents/video-creation/AGENT.md)
- [16 个子 Skill 与工具范围](../../agents/video-creation/agent.yaml)
- [故事选择与阶段确认](../../agents/video-creation/contracts/conversation.md)
- [工作流与退出条件](../../agents/video-creation/contracts/workflow.yaml)
- [建绘通工具契约](../../agents/video-creation/contracts/tools.yaml)
- [审阅清单](../../agents/video-creation/REVIEW.md)

v0.3.0 已实现对话界面与运行引擎，流程覆盖导演检查、用户主动润色、时长分析、创意、素材节点及回填、分镜、整片 BGM、视频片段、拼接合成与交付。定义采用通用故事输入和产物模板，不绑定具体项目案例。

当前仅完成 Agent 定义构建，运行引擎、业务接口和生成服务接入仍属于后续开发范围。
