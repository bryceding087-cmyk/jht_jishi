# 状态与产物约定

> 本文件保留完整逻辑产物设计；v0.3 的实际数据库字段和阶段 JSON 以 agent_runtime.py 及 runtime.md 为准，examples 中的完整设计快照不是 HTTP 响应样本。

本文为审阅用数据契约，字段由后续建绘通运行层实现。运行记录独立于现有脚本 JSON 存储，不能假定新增字段会被现有 `normalise_script` 保留。

## 通用产物

每个产物具有 `id`、`type`、`revision`、`run_id`、`project_id`、`status`、`source_refs`、`depends_on`、`payload` 和创建时间。`status` 为 `draft`、`ready`、`approved`、`stale` 或 `failed`。`depends_on` 引用具体产物版本，`source_refs` 指向原始用户消息、模板或工具回执。

用户可见资产 ID、生成任务 ID、画布节点 ID 和 Agent 产物 ID 分开记录。一个角色可以有多张参考图；一个视频任务可以产生一个新素材版本；同一素材可以被多个节点复用。

## RunSnapshot

| 字段 | 约定 |
| --- | --- |
| `id`, `project_id`, `revision` | 运行与项目身份；新项目绑定前 project_id 可为 null |
| `definition_version` | 本运行采用的 Agent 定义版本，当前为 0.3.0 |
| `intent` | `full_video`、`stage_artifact` 或 `revision` |
| `phase` | 来自 workflow.yaml 的阶段 ID |
| `status` | `draft`、`running`、`waiting_user`、`waiting_tool`、`paused`、`blocked`、`failed`、`completed`、`cancelled` |
| `interaction_mode` | `guided` 或用户授权的 `autonomous` |
| `source` | 原始剧情、来源消息、修订号；原文不可被方案覆盖 |
| `working_story_ref` | 当前采用的 StorySource 或 StoryDraft 版本；未采用草稿不能替换它 |
| `story_decision` | 导演检查引用、S0 用户选择、对应消息和被接受的缺项；与费用授权分开 |
| `spec` | 比例、目标时长、分辨率、风格、用途；每个值记录来源和是否确定 |
| `artifact_refs` | 当前采用的产物版本与历史引用 |
| `approvals` | 确认卡、用户消息、内容版本、输入指纹和授权范围 |
| `budget` | 按账户与单位分开的上限、估算、预留、实际费用及未知项 |
| `tasks` | 请求、供应商任务、片段、输入版本、状态、尝试次数与费用 |
| `pending_questions`, `issues` | 问题、影响范围和当前阻断条件 |
| `next_actions` | 可恢复的下一步，不依赖模型记住隐含待办 |

`phase` 表示创作走到哪一步，`status` 表示当前在做、在等或已结束。一个片段完成不会使整个 Run 完成。

## 创作阶段数据

| 类型 | 必需内容 |
| --- | --- |
| `ContextSnapshot` | 当前项目与版本、节点摘要、可用素材与角色、已有运行、任务和模板引用 |
| `StorySource` | 原始故事正文、来源消息和版本；用户新增补充保存新版本，原始输入保持可追溯 |
| `DirectorReview` | operation、目标产物与故事版本、逐项结论、缺项及影响、保真检查、assessment 和 proceed_policy |
| `StoryDraft` | 原稿引用、授权的改写方式、修改稿、差异、补充设定、保留项检查、采用状态及依据 |
| `ModelPlan` | 各媒体类型的模型配置 ID、能力来源、规格、限制、兼容性、待补能力 |
| `BudgetEstimate` | 分批任务、每项数量与计费单位、估算来源、已知/未知费用、预算余量 |
| `StoryAnalysis` | 采用稿与导演结论、人物、场景、关键动作、情节 ID、台词清单、品牌文字、字数与语速假设、台词和整体时长估算、初步分段 |
| `CreativeBrief` | 主题、结构、角色与场景设定、风格、情绪、声音方向、素材需求、约束与假设；content_revision、binding_revision、素材回填表 |
| `AssetManifest` | 需求 ID、类型、用途、用户/库/生成来源、真实素材节点、任务及资产 ID、受保护路径、版本、检查状态、使用范围 |
| `Storyboard` | 有序 Shot 列表、总时长计划、内容覆盖表、素材与声音依赖、剪辑策略 |
| `PlannedTimeline` | 来源分镜版本、每段计划起止秒、预估对白区间、转场重叠、独立片尾、计划总时长；不要求已有视频文件 |
| `PromptSet` | 每段可见的画面提示词、运动提示词、共享约束版本、最终提交值及输入指纹 |
| `VideoBatch` | 分镜版本、已授权批次、每段任务及采用结果、实际长度、失败和质量状态 |

台词项 `VoiceLine` 包含 `id`、`speaker_id`、`source_text`、`approved_text`、`kind`（对白/旁白）、`must_preserve_exactly`、`shot_ids`、`voice_id`、`audio_asset_id` 与 `measured_duration_seconds`。内心活动、间接叙述另作 story beat，不自动混入对白清单。

故事入口审查的 `assessment` 为 `complete` 或 `needs_input`；`proceed_policy` 为 `normal`、`waiting` 或 `user_accepts_gaps`。沿用不完整原稿只改变继续策略，不把判断伪造为完整。其他导演审查操作使用逐项结论，不套用故事入口的判定值。

`StoryDraft` 原始状态为待采用，选择采用或明确授权自动采用后才成为工作稿。来源与保留项不因文字改写丢失；采用新稿时更新下游依赖，不让旧稿时长和新稿台词混用。

创意回填表包含需求 ID、用途、主体、资产 ID、声音 ID（适用时）、画布节点 ID、受保护路径及检查状态。只添加或更新绑定时增加 `binding_revision` 和产物版本，保持 `content_revision`；不会因此将基于同一创意内容生成的素材全部作废。替换素材内容时仍按实际依赖使下游失效。

每个 `Shot` 包含稳定 `id`、显示 `sequence`、场景 ID、预计时长、动作、景别、构图、光线、运镜、人物 ID、素材 ID、台词 ID、声音路径、参考输入角色、连续性要求和前置依赖。显示镜号可以调整，稳定 ID 不随重排变化。

与现有脚本衔接时维护 `shot_id → scene.index → video_node_id` 映射。现有 `scene_indices` 是整数镜号，重排后必须同步；素材文件类型使用 `image/video/audio`，人物/场景/旁白等用途放在 `category`。

## 声音与后期数据

| 类型 | 必需内容 |
| --- | --- |
| `VoicePlan` | 每个说话人的声音选择、实际锚定音频资产、音频节点、语言与情绪、原生对白或独立配音策略、同步能力；声音 ID 与正式台词音频分别记录 |
| `AudioManifest` | 正式台词或音乐资产、来源、关联台词/时间区间、实测长度、原声处理方式；使用视频原声时记录 source_media_asset_id、音轨和裁切区间 |
| `MusicPlan` | basis_kind（planned/actual）、来源时间线版本、绝对起止秒、情绪、能量、乐器、对白区间、留白、音乐提示词、采用 BGM 资产和后期适配说明 |
| `SubtitleTrack` | 语言、核对后的文本、每句绝对时间码、样式、关联台词与音频 |
| `Timeline` | 视频片段、源文件裁切、在成片中的位置、转场、音轨、字幕、品牌文字/Logo 层、输出规格 |
| `RenderArtifact` | 渲染任务、时间线版本、实际文件资产、实测时长、预览地址及编码信息 |

所有时间线区间采用秒，区间为左闭右开。视频源片段的 `source_in/source_out` 与成片 `start/end` 分开。台词时间码以最终采用的音轨为准，变更剪辑后重新对齐。

`PlannedTimeline` 在分镜阶段产生，支持视频生成前制作 BGM；`Timeline` 在采用实际片段后形成。计划区间不能冒充实测参数；后期从计划版转到实际版时优先复用已生成的 BGM，仅更新裁切、循环、淡出等安排。

品牌层保存用户原文、入点、出点、字体/字号、位置、颜色和动画；Logo 引用实际图片或矢量资产。独立片尾占用成片时间，叠在已有画面上的文字层不增加时长。

## 检查、修改与交付

| 类型 | 必需内容 |
| --- | --- |
| `QualityReport` | 检查目标及版本、检查方法、每项 pass/fail/not_checked、证据、问题定位与修复建议 |
| `CanvasProjection` | 产物与节点映射、连线、镜号映射、项目版本、Agent 管理范围与用户修改保护 |
| `RevisionPlan` | 用户原话、修改对象、旧新版本、直接与下游影响、可复用产物、需重新确认的范围 |
| `DeliveryPackage` | 当前成片资产与作品 ID、预览/下载、实际规格、采用的产物版本、质量报告及修改入口 |

检查区分“文档内容正确”“工具返回成功”“媒体内容通过”。质量检查不能只看任务状态。自动检查无法证明的视觉、台词听感或口型项目可以请求用户人工检查，但必须有具体的预览与待检查项。

必需检查未完成时，完整视频运行不得完成。人工确认可以作为该版本的检查证据；它不能代替缺失或无法播放的文件。用户主动接受的创作取舍记录为变更约束，再按新约束检查。

## 工具返回的辅助对象

`ProjectSnapshot` 为现有项目字段加宿主投影；`ModelCatalog`、`AssetList` 分别为脱敏模型与可访问资产列表；`TaskReceipt/TaskSnapshot` 遵循运行契约中的任务状态。`MediaInspection` 保存元数据、抽帧、转写与可完成的检查项目，缺少能力时相应字段为 null。

`AssetRef` 包含真实建绘通资产 ID、文件类型和受保护的预览位置；它不等于 Agent 产物 ID。`PresentationReceipt` 只说明卡片已显示，不能作为用户确认；`ControlReceipt` 分别记录运行控制状态和供应商是否真正取消；`TimelineRef`、`ArtifactRef`、`RunSnapshot` 表示已保存对象，而非模型编造的链接。
