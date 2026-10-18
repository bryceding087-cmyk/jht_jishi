"""Translate provider moderation failures without inspecting users' prompts."""
import json
import re

CONTENT_REJECTION_MESSAGE = '您生成的内容不合规，请修改后重试。'

# Match explicit provider rejection codes/messages, not topics in a prompt or
# generic HTTP failures (400/403 also cover invalid parameters and permissions).
_REJECTION = re.compile(
    r'sensitive[\s_-]*content[\s_-]*detected|'
    r'(?:input|output)[\w-]*content[\s_-]*(?:filter|blocked)|'
    r'content[\s_-]*(?:policy[\s_-]*violation|filter(?:ed)?|moderation|blocked)|'
    r'responsible[\s_-]*ai[\s_-]*policy[\s_-]*violation|'
    r'data[\s_-]*inspection[\s_-]*failed|'
    r'(?:copyright|trademark|likeness)[\s_-]*(?:violation|infringement)|'
    r'(?:safety[\s_-]*(?:violation|filter)|prohibited[\s_-]*content)|'
    r'(?:may contain|contains?)\s+(?:sensitive|inappropriate|unsafe)\s+(?:information|content)|'
    r'(?:violat\w*|blocked by|rejected by).{0,60}(?:content policy|safety policy|safety system|moderation)|'
    r'(?:内容|提示词|输入|输出|图片|图像|视频|音频|文本).{0,12}(?:不合规|违规|违反.{0,8}(?:政策|规定)|未通过.{0,6}审核|审核不通过|审核失败|涉敏|包含敏感|含有敏感)|'
    r'(?:内容|安全)审核.{0,8}(?:拦截|拒绝|不通过|失败)',
    re.IGNORECASE,
)


def normalize_content_error(value):
    """Preserve unrelated errors, including structured provider errors."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return CONTENT_REJECTION_MESSAGE if _REJECTION.search(text) else value
