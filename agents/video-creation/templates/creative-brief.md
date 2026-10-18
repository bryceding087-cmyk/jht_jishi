# 创意文档模板

本模板用于 `creative-design` 输出。花括号字段由当前项目内容替换；没有依据的参数写“待确定”并注明建议，不将模板占位符作为完成产物。

## 项目信息

作品名称：{{title}}  
版本与来源：{{revision_and_source}}  
采用故事与导演结论：{{working_story_ref_and_director_review_ref}}  
创意内容修订 / 素材绑定修订：{{content_revision}} / {{binding_revision}}  
用途与受众：{{purpose_and_audience}}  
画幅、时长目标、输出规格：{{spec_and_provenance}}

## 故事与表达

主题：{{theme}}  
人物关系与变化：{{character_arc}}  
开场、冲突、转折、结尾：{{story_structure}}  
关键情节及对应原文 ID：{{required_beats}}

## 视觉方向

风格、色彩、光线与时间变化：{{visual_direction}}  
角色设定及创意补充项：{{characters}}  
场景、道具与空间关系：{{locations_and_props}}

## 声音与文字

对白和旁白处理：{{voice_strategy}}  
音乐、环境音与留白：{{sound_direction}}  
精确保留台词及品牌文字：{{locked_text}}  
口型同步等前置依赖：{{audio_dependencies}}

| 说话人 ID | 人物或旁白 | 声音要求 | 锚定试听需求 ID | 正式台词策略 |
| --- | --- | --- | --- | --- |
| {{speaker_id}} | {{speaker_role}} | {{voice_direction}} | {{anchor_requirement_id}} | {{native_or_separate_speech}} |

## 素材与待决定事项

| 需求 ID | 文件类型 | 用途 | 优先来源 | 必要性 | 关联情节 |
| --- | --- | --- | --- | --- | --- |
| {{id}} | {{image_or_audio_or_video}} | {{purpose}} | {{source}} | {{requirement}} | {{beat_ids}} |

## 素材回填表

初稿尚未生成素材时填“待生成”并保留空引用；结果就绪后再依据真实回执回填，不将占位文件名当作实际路径。

| 需求 ID | 主体与用途 | 实际资产 ID / 版本 | 声音 ID（适用时） | 画布节点 ID | 受保护路径或预览引用 | 检查状态 |
| --- | --- | --- | --- | --- | --- | --- |
| {{requirement_id}} | {{subject_and_purpose}} | {{asset_ref}} | {{voice_id_or_na}} | {{node_id}} | {{protected_location}} | {{check_state}} |

回填来源清单：{{asset_manifest_ref_and_voice_plan_ref}}  
本轮画布整理结果：{{canvas_projection_ref}}  
G2 采用的创意与素材版本：{{approved_refs_or_pending}}

## 回读与自审

实际回读的保存版本：{{readback_revision}}  
逐条台词、人物事实、关键情节和动作顺序：{{fidelity_and_coverage_result}}  
真实文件、图片及声音与节点绑定检查：{{asset_binding_result}}  
用户已接受的故事缺项、创意补充与待决定项：{{accepted_gaps_and_open_decisions}}  
导演与素材质量报告、未检查项：{{review_refs_and_unchecked_items}}

仅回填路径和引用时增加 binding_revision；更改剧情、人物或视觉设定时增加 content_revision 并分析下游影响。分镜读取 G2 所采用的回填版本。
