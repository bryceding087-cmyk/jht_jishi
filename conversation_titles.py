"""Short first-message labels, refined by the Agent's existing model call."""
import re

MAX_TITLE_LENGTH = 16


def short_title(text, kind='agent', *, summarize=True):
    text = str(text or '').strip()
    text = re.sub(r'https?://\S+|@[\w.\-]+\.(?:png|jpe?g|webp|mp4|wav|mp3)\b', '', text, flags=re.I)
    text = re.sub(r'[@#*`]+', '', text).strip()
    named = re.search(r'《([^》\n]+)》', text)
    if named:
        text = named[1]
    elif summarize:
        text = re.sub(r'^(?:你好[，,！!\s]*|请问|请你|请|麻烦你|麻烦|能否|能不能|可以|帮我|帮忙|给我|为我|我想要|我想|我需要|想要|一下|\s)+', '', text)
        text = re.sub(r'^(?:生成|创建|制作|创作|画出|画(?!布|面|风|作)|写出|写(?!作|实|意|真)|设计|做)(?:一下)?(?:一[个张幅段条部篇首份])?(?:关于|有关)?', '', text)
        text = re.sub(r'^(?:图片|视频|音频|剧本|短剧|故事|分镜)\s*[:：]\s*', '', text)
        text = re.sub(r'^(?:please\s+)?(?:create|generate|draw|write|make)\s+(?:(?:an?|the)\s+)?(?:(?:image|video|picture)\s+of\s+)?', '', text, flags=re.I)
        text = re.split(r'[\n。！？!?；;，,]', text, maxsplit=1)[0]
        text = re.sub(r'(?:的(?:一[个张幅段条部篇首份])?)?(?:图片|视频|音频|剧本|短剧|照片)(?:吧|一下)?$', '', text)
        text = re.sub(r'([\u4e00-\u9fff])(?:正在|在)(?=打|踢|跳|跑|唱|弹|吃|喝|看|玩)', r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip(' ：:，,。.!！?？；;\"“”「」《》')
    if len(text) > MAX_TITLE_LENGTH:
        cut = text[:MAX_TITLE_LENGTH]
        if cut[-1].isascii() and cut[-1].isalpha() and text[MAX_TITLE_LENGTH].isascii() and text[MAX_TITLE_LENGTH].isalpha() and ' ' in cut:
            cut = cut.rsplit(' ', 1)[0]
        text = cut.rstrip(' 的在与和，,：:、-')
    return text or {'image': '图片创作', 'video': '视频创作', 'audio': '音频创作'}.get(kind, '新对话')
