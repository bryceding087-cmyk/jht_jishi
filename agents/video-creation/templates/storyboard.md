# 分镜模板

使用真实项目内容替换占位字段。完整结构化数据遵循[产物约定](../contracts/artifacts.md)，本表用于对话和脚本界面审阅。

作品与分镜版本：{{title_and_revision}}  
原文、创意、素材版本：{{source_refs}}  
当前采用故事与回填后的创意内容/绑定修订：{{working_story_and_creative_revisions}}  
G2 有效依据或仅制作分镜的范围说明：{{g2_or_scope_exception}}  
目标时长与估算/实测说明：{{duration_basis}}

| 稳定片段 ID | 显示镜号 | 计划时长 | 场景与动作 | 构图、景别、光线 | 运镜 | 台词 ID 与原文 | 素材 ID | 声音方式 | 前置依赖 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| {{shot_id}} | {{sequence}} | {{duration}} | {{action}} | {{visual}} | {{camera}} | {{lines}} | {{assets}} | {{audio}} | {{dependencies}} |

## 内容覆盖

| 原文情节或文字 ID | 表达方式 | 对应片段或后期轨 | 检查结论 |
| --- | --- | --- | --- |
| {{source_id}} | {{expression}} | {{target}} | {{result}} |

## 时长与生成策略

片段总长度、转场重叠、独立片尾与最终计划时长：{{timeline_math}}  
音频实测结果或待复核项：{{speech_timing}}  
模型能力检查、并行组及顺序依赖：{{generation_strategy}}  
费用与确认范围：{{batch_scope}}

## 计划时间线与声音前置项

PlannedTimeline 引用与来源分镜版本：{{planned_timeline_ref_and_storyboard_revision}}

| 片段或轨道 ID | 计划起止秒 | 台词区间及估算/实测依据 | 转场或片尾对时长的影响 | BGM 情绪与留白 |
| --- | --- | --- | --- | --- |
| {{shot_or_track_id}} | {{start_end_seconds}} | {{speech_intervals_and_basis}} | {{overlap_or_extra_duration}} | {{music_direction}} |

每位说话人的音色锚定音频：{{anchor_asset_refs}}  
必须在视频前完成的正式配音与同步能力：{{pre_video_audio_dependencies}}  
G3 确认的版本、付费范围及样片/无音乐例外：{{g3_scope}}

分镜确认后先核验前置正式配音，再生成整片 BGM，随后生成视频。计划时间线不要求已有视频文件；正式配音导致时长变化时先修订此表，不能把估算值写成实测。
