"""Trusted visual recipes, selection validation and provider prompt composition."""
from functools import lru_cache
import json
from pathlib import Path
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator


@lru_cache(maxsize=1)
def style_catalog():
    return json.loads((Path(__file__).parent / 'resources/visual-styles.json').read_text(encoding='utf-8'))


class VisualStyleSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=80)
    custom_prompt: str = Field(default='', max_length=1500)

    @model_validator(mode='after')
    def valid_selection(self):
        self.custom_prompt = self.custom_prompt.strip()
        if self.id == 'custom':
            if not self.custom_prompt:
                raise ValueError('请填写自定义风格提示词')
        elif self.id not in {s['id'] for s in style_catalog()['styles']}:
            raise ValueError('所选风格不存在，请重新选择')
        elif self.custom_prompt:
            raise ValueError('预设风格不能携带自定义提示词')
        return self


def public_styles():
    return {'version': style_catalog()['version'], 'styles': [
        {key: s[key] for key in ('id', 'display_name', 'source_name', 'category', 'aliases', 'summary', 'preview_url')}
        for s in style_catalog()['styles']]}


def resolve_style(selection):
    if not selection:
        return None
    value = VisualStyleSelection.model_validate(selection)
    if value.id == 'custom':
        return {'id': 'custom', 'name': '自定义风格', 'prompt': value.custom_prompt, 'avoid': [], 'video_consistency': [], 'version': 'custom-1'}
    s = next(item for item in style_catalog()['styles'] if item['id'] == value.id)
    return {'id': s['id'], 'name': s['display_name'], 'prompt': s['image_style_prompt_zh'],
            'avoid': s['avoid'], 'video_motion': s['video_motion'], 'video_consistency': s['video_consistency'],
            'version': style_catalog()['version']}


def adapt_style_text(text, subject):
    """Drop default art directions for dimensions the user has specified.

    This is a conservative conflict filter, not a semantic rewriting model.
    Do not add a style's example subject or example-generation prompt.
    """
    dimensions = [
        (r'背景|底色|background|backdrop', r'背景|纸色|纸张|纸白|留白|底色'),
        (r'光照|光线|灯光|逆光|侧光|顶光|柔光|硬光|打光|lighting', r'光|阴影|明暗'),
        (r'色调|配色|饱和|黑白|单色|彩色|颜色|palette|monochrome', r'色|调色'),
        (r'俯拍|仰拍|平视|特写|全景|近景|构图|居中|对称|composition|close.up', r'构图|平视|景深|特写|全景|近景|对称'),
    ]
    exclusions = [style for user, style in dimensions if re.search(user, subject, re.I)]
    if not exclusions:
        return text
    clauses = re.split(r'[，。；;\n]+', text)
    return '；'.join(clause.strip() for clause in clauses if clause.strip()
                    and not any(re.search(pattern, clause) for pattern in exclusions))


def compose_visual_prompt(prompt, snapshot, kind='image'):
    if not snapshot:
        return prompt
    custom = snapshot['id'] == 'custom'
    recipe = snapshot['prompt'] if custom else adapt_style_text(snapshot['prompt'], prompt)
    parts = [prompt, f"画面风格（{snapshot['name']}）：{recipe}"]
    avoid = [adapt_style_text(item, prompt) for item in snapshot.get('avoid', [])]
    if any(avoid):
        parts.append('风格避免项：' + '；'.join(filter(None, avoid)))
    if kind == 'video':
        # Only suggest camera motion when the user has not specified it.
        if not re.search(r'镜头|运镜|推近|拉远|摇|跟拍|固定机位|camera|pan|zoom|dolly', prompt, re.I) and snapshot.get('video_motion'):
            parts.append('运动建议：' + snapshot['video_motion'])
        if snapshot.get('video_consistency'):
            parts.append('跨帧一致性：' + '；'.join(snapshot['video_consistency']))
    parts.append('仅将风格用于视觉表现，保留原始内容中的主体、身份、动作、场景和文字，不添加示例人物或情节。')
    effective = '\n\n'.join(parts)
    if len(effective) > 4000:
        raise ValueError('内容与风格提示词合计超过 4000 字，请缩短描述或自定义风格后重试')
    return effective


def style_task_fields(selection, prompt, kind):
    snapshot = resolve_style(selection)
    if not snapshot:
        return {}
    return {'style_snapshot': snapshot, 'effective_prompt': compose_visual_prompt(prompt, snapshot, kind)}
