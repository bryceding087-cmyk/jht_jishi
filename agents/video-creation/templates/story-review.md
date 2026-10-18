# 故事入口检查模板

本模板用于导演的 assess_story 操作。创意、分镜和成片审查使用对应产物的覆盖检查，不套用入口判定。

当前采用故事与版本：{{working_story_ref}}  
用户制作意图与已授权改写范围：{{intent_and_rewrite_scope}}

| 检查对象 | 原文依据 | 是否足够继续 | 具体缺项及影响 |
| --- | --- | --- | --- |
| 主体与人物关系 | {{subjects}} | {{result}} | {{gap_or_none}} |
| 事件、过程或因果 | {{events}} | {{result}} | {{gap_or_none}} |
| 结尾表达或有意留白 | {{ending}} | {{result}} | {{gap_or_none}} |
| 关键事实与矛盾 | {{facts}} | {{result}} | {{gap_or_none}} |

判定 assessment：{{complete_or_needs_input}}  
继续策略 proceed_policy：{{normal_or_waiting_or_user_accepts_gaps}}  
精确保留的对白、旁白、事实与品牌文案：{{locked_items}}  
用户选择及对应消息/故事版本：{{story_action_or_not_required}}  
下一步：{{next_action}}

完整故事直接分析，不主动改写。关键缺项展示补充故事、补全并润色、沿用原文三个选择；开放式结尾不自动判为缺项。用户接受缺项只改变继续策略，不能将判定改写为完整。

如生成 StoryDraft，另附原稿引用、改写授权、完整修改稿、逐项差异、补充设定、保留项检查和采用状态；待采用草稿不替换当前故事。
