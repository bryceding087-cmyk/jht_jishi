# 逐片段提示词模板

用于 `prompt-composition`。内容保存到建绘通脚本可见字段，不在视频提交时追加未展示的隐藏提示词。

片段与版本：{{shot_id_and_revision}}  
对应脚本节点与镜号：{{script_binding}}  
模型、时长、画幅、分辨率：{{model_spec}}  
参考图及输入角色：{{reference_bindings}}

## 画面提示词 → storyboard_prompt

{{主体身份与外观、场景、构图、景别、光线、风格，以及必要的可见连续性约束}}

## 运动提示词 → motion_prompt

{{人物动作先后、表情变化、物体运动、镜头移动、速度、停顿与转场}}

## 关联信息

台词 ID 与声音策略：{{line_ids_and_audio_strategy}}  
后期字幕与品牌层需求：{{postproduction_text}}  
共享约束版本：{{shared_constraints_revision}}  
合并后的实际提交值：由建绘通按可见字段计算。  
输入指纹：由宿主保存输入时计算并回传。

检查画面与动作是否矛盾，台词能否放入片段，以及合并长度是否超过实际接口限制。只将能被当前模型执行的输入发送给生成服务。
