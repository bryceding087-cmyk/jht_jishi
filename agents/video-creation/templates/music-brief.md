# 配乐方案模板

作品与音乐版本：{{title_and_revision}}  
操作：{{generate_full_bgm_or_align_to_timeline}}  
依据类型 basis_kind：{{planned_or_actual}}  
采用的 PlannedTimeline 或 Timeline 版本：{{timeline_ref}}  
来源分镜与声音版本：{{storyboard_and_voice_refs}}  
时长依据（计划视频长度 / 已实测台词 / 实测剪辑）：{{duration_basis}}  
用户声音要求：{{user_preference}}

| 绝对起止秒 | 画面或情绪 | 能量变化 | 乐器与节奏 | 对白/旁白区间 | 留白与混音 |
| --- | --- | --- | --- | --- | --- |
| {{start_end}} | {{emotion}} | {{energy}} | {{instruments}} | {{speech}} | {{mixing}} |

整体音乐提示：{{music_prompt}}  
人声要求：{{instrumental_or_vocal}}  
所选模型支持的结构标签：{{supported_structure}}  
既有 BGM 复用判断或本次生成任务：{{reuse_decision_or_task_ref}}  
实际 BGM 资产与音频节点：{{bgm_asset_and_node_refs}}  
实际音频长度与剪辑策略：{{measured_result_and_edits}}  
对白期间音量、淡入淡出及结尾处理：{{mix_parameters}}

## 后期适配记录

原计划与实际时间线差异：{{timeline_delta}}  
保留的 BGM 资产：{{retained_asset_ref}}  
裁切、循环、音量及淡入淡出安排：{{adaptation_edits}}  
确需重制时的理由、范围与授权依据：{{regeneration_if_needed}}

默认在视频生成前使用 planned 版制作整片 BGM，所需前置正式台词应先完成时长检查，无需等待视频文件。后期使用 actual 版适配既有音乐；更新方案并不等于重新提交音乐生成。时间线变更后复核表中区间。仅在模型确实支持时传结构标签；无依据的模型参数不写进调用。
