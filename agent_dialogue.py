"""Short, public creative updates grounded in the saved Agent state.

This module does not run models or tools. Optional model-authored copy explains
the creative proposal; production facts and next steps come from the host.
"""
import math
import re


DIALOGUE_PROMPT = (
    '\n额外返回可选 user_message:string，写给用户的一至两句自然中文，最多110字。'
    '结合这次故事的具体人物、场景或诉求，说清你理解的重点，或解释当前方案一个可供用户判断的创作取舍及其效果。'
    '这是简短的公开创作说明，不输出私密思维链、逐步推理、自言自语或内部检查过程。'
    '像和用户一起做作品，直接说具体内容，不套用“很棒的想法”，不重复上一阶段的开场，不写小标题和JSON。'
    '只依据本次输入和已有方案；不得声称搜索过热点、观看过视频、试听过声音或已完成任何工具操作。'
    '不要在 user_message 中报告生成数量、时长、素材完成状态、下一步操作或要求用户确认，这些由宿主按真实状态补充。'
    '严格遵守 preferences 中的制作范围、配音和音乐开关；没有对白不虚构对白，不新增用户未要求的创作要求。'
    '若本次是修改或普通询问，直接回应用户要求或问题，不将询问描述成已执行修改。'
)


PHASE_NAMES = {
    'story_review': '梳理故事', 'analysis': '安排内容节奏', 'creative': '设计创意',
    'assets': '准备参考素材', 'storyboard': '规划分镜', 'music': '准备声音',
    'video': '生成分镜视频', 'postproduction': '合成成片', 'review': '检查成片',
    'delivery': '交付',
}


def _text(value, limit=100):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'\s+', ' ', value).strip()
    return value if len(value) <= limit else value[:limit].rstrip('，,；;：: ') + '…'


def _payload(run, name):
    artifact = run.get('artifacts', {}).get(name, {})
    value = artifact.get('payload', {}) if isinstance(artifact, dict) else {}
    return value if isinstance(value, dict) else {}


def _positive(value):
    if isinstance(value, bool):
        return 0
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return 0
    return value if math.isfinite(value) and value > 0 else 0


def _seconds(value):
    return f'{_positive(value):g}'


def _sentence(text):
    return text if not text or text[-1] in '。！？!?.…' else text + '。'


def _join(*parts):
    return ''.join(_sentence(part) for part in parts if part)


def _topic(run):
    title = _payload(run, 'creative').get('title') or _payload(run, 'director').get('title')
    if not title:
        title = re.split(r'[\n。！？，,：:]', str(run.get('working_story') or run.get('source') or run.get('title') or '这次创作'), maxsplit=1)[0]
        title = re.sub(r'[「」“”‘’"\u0027]', '', title)
    return '「' + _text(title, 32).strip('「」“”') + '」'


def _model_message(result, run):
    """Treat optional presentation fields as untrusted, bounded plain text.

    The host supplies operation outcomes. Discard copy with tool/completion
    claims rather than presenting a model's claim as evidence of an action.
    """
    if not isinstance(result, dict):
        return ''
    raw = result.get('user_message') or result.get('creative_rationale')
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 220:
        return ''
    if re.search(r'```|[{}<>]|思维链|内心独白|逐步推理|内部推理|chain.of.thought', raw, re.I):
        return ''
    if re.search(r'搜索|检索|联网|热点|热度|看过|观看过|看了.*视频|听过|试听过|试听了|工具调用|已(?:经)?(?:生成|保存|合成|完成|检查|准备)|检查通过|(?:生成|制作|合成|准备)好了', raw):
        return ''
    if re.search(r'\d+\s*(?:秒|分钟|段|镜|张|条)', raw):
        return ''
    prefs = run.get('preferences', {})
    if not prefs.get('voice', True) and re.search(r'配音|旁白|对白|音色|台词', raw):
        return ''
    if not prefs.get('music', True) and re.search(r'配乐|音乐|BGM', raw, re.I):
        return ''
    return _text(raw, 160)


def _scenes(run, result=None):
    source = result if isinstance(result, dict) and isinstance(result.get('scenes'), list) else _payload(run, 'storyboard')
    return [scene for scene in source.get('scenes', []) if isinstance(scene, dict)]


def _sound_plan(run):
    prefs = run.get('preferences', {})
    parts = []
    if prefs.get('voice', True) and _payload(run, 'director').get('locked_lines'):
        parts.append('正式配音')
    if prefs.get('music', True):
        parts.append('整片 BGM')
    return '和'.join(parts)


def _scene_name(scene):
    return _text(scene.get('description'), 38) if isinstance(scene, dict) else ''


def dialogue(stage, run, result=None, **facts):
    """Return one public update; callers emit it only when nonempty.

    ``run`` must reflect the completed operation (freshly read after patching).
    ``result`` is the validated stage response, optionally with user_message.
    Optional facts: video_saved(index=one_based, scene=dict), review(duration),
    resume(action='resume'|'retry'), revision(summary=str), progress().
    No helper mutates the run, schedules work, or creates a confirmation gate.
    """
    prefs = run.get('preferences', {})
    storyboard_only = prefs.get('scope') == 'storyboard'
    result = result if isinstance(result, dict) else {}
    authored = _model_message(result, run)
    topic = _topic(run)

    if stage == 'received':
        target = _positive(prefs.get('target_duration'))
        timing = f'，按你设定的 {_seconds(target)} 秒来安排节奏' if target else ''
        return f'收到，我先梳理{topic}的故事重点{timing}。' + (
            '这次先把创意和分镜做清楚，方便你确认方向。' if storyboard_only else
            '我会先把创意和分镜安排清楚，再按确认的方案制作。')

    if stage == 'story_review':
        intro = authored or f'我会围绕{topic}推进创作，你原文中的明确台词和片尾文字会保留。'
        if result.get('complete') or run.get('accepted_gaps'):
            return _join(intro, '接下来先把故事节奏和所需时长理清楚')
        gaps = [_text(gap, 35) for gap in result.get('gaps', []) if isinstance(gap, str) and gap.strip()][:3]
        return _join(intro, '还有几个会影响呈现的细节：' + '；'.join(gaps) if gaps else '',
                     '你可以补充，也可以让我补全一版供你选择；想保留留白也可以沿用原文')

    if stage == 'analysis':
        duration = _positive(result.get('estimated_duration'))
        timing = f'按现有内容估算，需要约 {_seconds(duration)} 秒' if duration else '故事节奏已整理好'
        return _join(authored or f'我把{topic}按故事内容拆成了节奏段落', timing,
                     '实际镜头会按所选模型支持的时长安排。先选好画幅、风格和制作范围，我就沿着这个方向继续')

    if stage == 'creative':
        visuals = result.get('visuals') or _payload(run, 'creative').get('visuals', [])
        names = [_text(v.get('name'), 22) for v in visuals if isinstance(v, dict) and v.get('name')][:2]
        fallback = f'{topic}的创意方案已整理好'
        if names:
            fallback += '，视觉设定重点放在' + '、'.join(names)
        if storyboard_only:
            next_step = '接下来把方案展开为镜头顺序和画面描述，你可以直接告诉我想调整哪一处'
        elif visuals or (prefs.get('voice', True) and (result.get('speakers') or _payload(run, 'creative').get('speakers'))):
            next_step = '接下来准备方案中的参考素材，让你先确认视觉方向' + ('和音色' if prefs.get('voice', True) and (result.get('speakers') or _payload(run, 'creative').get('speakers')) else '')
        else:
            next_step = '接下来把这个方向细化到分镜里'
        return _join(authored or fallback, next_step)

    if stage == 'assets':
        bindings = [b for b in run.get('bindings', {}).values() if isinstance(b, dict) and b.get('asset_id')]
        images = [b for b in bindings if b.get('kind') == 'image']
        voices = [b for b in bindings if b.get('kind') == 'audio'] if prefs.get('voice', True) else []
        ready = ([f'{len(images)} 张参考图片'] if images else []) + ([f'{len(voices)} 段音色试听'] if voices else [])
        if not ready:
            return '这一版不需要额外参考素材。你可以先看创意方案，确认后我会继续规划分镜。'
        focus = ('人物造型与画面风格' if images else '') + ('、' if images and voices else '') + ('声音是否符合角色' if voices else '')
        return f'{"、".join(ready)}已准备好。请重点看看{focus}；需要修改直接告诉我，满意的话就按这个方向规划分镜。'

    if stage == 'storyboard':
        scenes = _scenes(run, result)
        total = sum(_positive(s.get('duration')) for s in scenes)
        overview = f'分镜已排成 {len(scenes)} 段，计划 {_seconds(total)} 秒'
        if storyboard_only:
            return _join(authored, overview, '创意与分镜已保存到当前对话，这次先交付分镜。你可以按镜头告诉我想调整的画面或节奏')
        sounds = _sound_plan(run)
        next_step = f'确认后我会先准备{sounds}，再逐段生成视频' if sounds else '确认后我会按分镜逐段生成视频'
        return _join(authored, overview, '请重点看故事顺序和每镜的动作安排', next_step)

    if stage == 'music_ready':
        ready = []
        speech = [s for s in run.get('speech', {}).values() if isinstance(s, dict) and s.get('asset_id')]
        if prefs.get('voice', True) and speech:
            ready.append(f'{len(speech)} 段正式配音')
        bgm = run.get('bgm') or {}
        if prefs.get('music', True) and bgm.get('asset_id'):
            ready.append('整片 BGM')
        if not ready:
            return ''
        music_note = '成片会使用这首整片配乐，让各镜头之间的音乐保持连贯。' if prefs.get('music', True) and bgm.get('asset_id') else ''
        return f'{"和".join(ready)}已准备好。{music_note}接下来开始制作分镜视频。'

    if stage == 'video_saved':
        scenes = _scenes(run)
        index = facts.get('index', 1)
        if not isinstance(index, int) or not 1 <= index <= len(scenes):
            return ''
        scene = facts.get('scene') or scenes[index - 1]
        label = _scene_name(scene)
        prefix = f'第 {index}/{len(scenes)} 段视频已保存。' + (f'这一镜是「{label}」。' if label else '')
        return prefix + ('接下来制作下一段。' if index < len(scenes) else '分镜片段已齐，接下来把它们接成完整故事。')

    if stage == 'postproduction':
        scenes = _scenes(run)
        ready = sum(bool((run.get('videos', {}).get(s.get('id')) or {}).get('asset_id')) for s in scenes)
        if not scenes or ready != len(scenes):
            return ''
        parts = []
        if prefs.get('voice', True) and run.get('speech'):
            parts.append('正式配音')
        if prefs.get('music', True) and (run.get('bgm') or {}).get('asset_id'):
            parts.append('前面准备的整片 BGM')
        sound = '，并接入' + '和'.join(parts) if parts else ''
        return f'{len(scenes)} 段视频都已就绪，我会按分镜顺序拼接{sound}。合成后你就可以观看整片，再一起调整具体镜头。'

    if stage == 'review':
        duration = _positive(facts.get('duration'))
        duration_note = f'，实测 {_seconds(round(duration, 1))} 秒' if duration else ''
        focus = ['故事节奏', '人物和动作的连贯性']
        if prefs.get('voice', True) and run.get('speech'):
            focus.append('台词听感')
        if prefs.get('music', True) and (run.get('bgm') or {}).get('asset_id'):
            focus.append('配乐的情绪')
        if prefs.get('subtitles', True) and _payload(run, 'director').get('locked_lines'):
            focus.append('字幕时机')
        return f'{topic}的完整视频已合成{duration_note}。请播放看看{"、".join(focus)}；想改哪一镜、哪个时间点，直接告诉我就行。'

    if stage == 'resume':
        scenes = _scenes(run)
        videos = run.get('videos', {})
        count = sum(bool((videos.get(s.get('id')) or {}).get('asset_id')) for s in scenes)
        pending = run.get('pending_review')
        if pending:
            return '接着来，之前的方案和产物都保留着。先完成当前确认卡，我就从这里继续。'
        prefix = f'已有 {count} 段视频会继续复用，' if count else '之前已完成的步骤会继续复用，'
        step = PHASE_NAMES.get(run.get('phase'), '当前步骤')
        return f'接着来，{prefix}' + (f'我会重试{step}中未完成的步骤。' if facts.get('action') == 'retry' else f'我会从{step}接着做。')

    if stage == 'progress':
        status = run.get('status')
        phase = PHASE_NAMES.get(run.get('phase'), '制作')
        if status == 'completed':
            return f'{topic}的' + ('创意和分镜已经完成，你可以继续提出调整。' if storyboard_only else '成片已经完成并保存，你可以播放查看或继续提出调整。')
        if status == 'waiting_user':
            return f'已经推进到{phase}。现在需要你看看当前确认卡；你可以按方案继续，也可以直接说出想改的地方。'
        if status in ('paused', 'cancelled', 'blocked'):
            state = {'paused': '已暂停', 'cancelled': '已取消', 'blocked': '遇到了一处需要处理的问题'}[status]
            return f'目前在{phase}，{state}。已经保存的产物都还在；' + ('具体原因见上方提示，处理后可以从这里继续。' if status == 'blocked' else '需要继续时可以从已有进度接着做。' if status == 'paused' else '可以在当前对话中查看。')
        return f'我正在{phase}，会沿着已确认的方案推进。有新的想法也可以直接告诉我。'

    if stage == 'revision':
        summary = _text(facts.get('summary') or result.get('summary'), 220)
        if result.get('kind') == 'reply':
            return _sentence(summary or authored or '你可以指出想了解的镜头或制作步骤，我会结合当前方案说明。')
        return _join(authored or summary or '你的修改要求已记录，我会按涉及的部分更新')

    if stage == 'delivery':
        if storyboard_only:
            return f'{topic}的创意与分镜已保存到当前对话。想调整哪个镜头，可以在这里接着改。'
        return f'{topic}已经完成并加入生成历史。之后想调整某个镜头或片尾文字，也可以在这里接着改。'

    return ''
