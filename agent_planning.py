"""Deterministic storyboard timing repair, without provider calls or persistence."""

from copy import deepcopy
import math

from fastapi import HTTPException


MAX_SCENES = 80
MAX_SECONDS = 1200
CONTINUATION_NOTE = '续镜：承接上一段动作与时间进程，继续完成剩余动作，不从头重演；保持人物、场景、构图和光线连续。'


def _number(value, default):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) and number > 0 else default


def _seconds(value, default):
    return math.ceil(_number(value, default))


def _partition(target, durations, minima=()):
    """Least total >= target, then fewest clips; retain existing clip lower bounds."""
    if target > MAX_SECONDS:
        raise HTTPException(422, '当前一次制作总长不能超过 20 分钟，请拆分项目')
    ceiling = min(MAX_SECONDS, max(target, sum(minima)) + max(durations) - 1)
    states = {0: ()}
    best = None
    for count in range(1, MAX_SCENES + 1):
        minimum = minima[count - 1] if count <= len(minima) else durations[0]
        choices = [duration for duration in durations if duration >= minimum]
        next_states = {}
        for total, parts in states.items():
            for duration in reversed(choices):
                candidate_total = total + duration
                if candidate_total > ceiling:
                    continue
                candidate = (*parts, duration)
                if candidate > next_states.get(candidate_total, ()):
                    next_states[candidate_total] = candidate
        states = next_states
        if not states:
            break
        if count >= max(1, len(minima)):
            candidates = [total for total in states if total >= target]
            if candidates:
                total = min(candidates)
                candidate = states[total]
                if best is None or (total, count) < (sum(best), len(best)):
                    best = candidate
                    ceiling = total
                if total == target:
                    break
        if best is not None and (count + 1) * durations[0] > sum(best):
            break
    if best is None:
        raise HTTPException(422, '镜头时长超出本次可制作范围，最多 80 镜、20 分钟')
    return list(best)


def _continuation(scene, root, number):
    result = deepcopy(scene)
    result.update(id=f'{root}_part_{number}', continuation_of=root, line_ids=[])
    prompt = str(result.get('prompt') or '')
    if CONTINUATION_NOTE not in prompt:
        result['prompt'] = CONTINUATION_NOTE + '\n' + prompt
    return result


def normalize_storyboard(story, durations, line_seconds=None):
    """Return an independent executable plan and human-readable timing changes.

    ``line_seconds`` maps a locked line ID to its total required playback time,
    including pauses. Lines remain on the root shot and may play continuously
    over its following ``continuation_of`` shots. Reused videos are not subject
    to generation-duration profiles; their actual lengths need host validation.
    """
    if not isinstance(story, dict) or not isinstance(story.get('scenes'), list) or not story['scenes']:
        raise HTTPException(422, '分镜需包含有效镜头')
    supported = sorted({value for value in durations if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 60})
    if not supported:
        raise HTTPException(422, '所选视频模型尚未配置有效的整秒时长能力')
    updated = deepcopy(story)
    scenes = updated['scenes']
    if len(scenes) > MAX_SCENES or any(not isinstance(scene, dict) for scene in scenes):
        raise HTTPException(422, '分镜需包含 1 至 80 个结构化镜头')
    adjustments = []
    output = []
    line_seconds = line_seconds or {}
    index = 0
    used_ids = set()
    while index < len(scenes):
        root = scenes[index]
        root_id = str(root.get('id') or f'shot_{index + 1}')
        if root_id in used_ids:
            raise HTTPException(422, '分镜编号重复，请重新规划')
        root['id'] = root_id
        group = [root]
        index += 1
        while index < len(scenes) and scenes[index].get('continuation_of') == root_id:
            group.append(scenes[index])
            index += 1
        line_ids = list(dict.fromkeys(lid for member in group for lid in (member.get('line_ids') or [])))
        required = math.ceil(sum(_number(line_seconds.get(lid), 0) for lid in line_ids))
        planned = []
        for member in group:
            requested = _seconds(member.get('duration'), supported[0])
            if member.get('video_asset_id'):
                if requested > 60:
                    raise HTTPException(422, '复用视频的单镜计划时长不能超过 60 秒')
                parts = [requested]
            else:
                parts = _partition(requested, supported)
            if len(parts) > 1:
                adjustments.append(f'镜头 {root_id} 的 {requested} 秒画面已拆为 '+ ' + '.join(map(str, parts)) + ' 秒连续镜头。')
            elif member.get('duration') != parts[0]:
                adjustments.append(f'镜头 {root_id} 时长已调整为 {parts[0]} 秒。')
            for duration in parts:
                planned.append({**deepcopy(member), 'duration': duration})
        old_total = sum(member['duration'] for member in planned)
        if required > old_total:
            if any(member.get('video_asset_id') for member in planned):
                # Preserve the supplied footage. Extra generated continuations
                # provide space for speech without pretending footage is longer.
                extra = _partition(required - old_total, supported)
                source = {k: v for k, v in root.items() if k != 'video_asset_id'}
                planned.extend({**deepcopy(source), 'duration': duration} for duration in extra)
            else:
                parts = _partition(required, supported, [member['duration'] for member in planned])
                planned = [{**deepcopy(planned[i] if i < len(planned) else root), 'duration': duration}
                           for i, duration in enumerate(parts)]
            adjustments.append(f'镜头 {root_id} 已为完整台词预留 {sum(member["duration"] for member in planned)} 秒连续画面。')
        for number, member in enumerate(planned):
            if number:
                member = _continuation(member, root_id, number + 1)
            else:
                member.update(id=root_id, line_ids=line_ids)
                member.pop('continuation_of', None)
            if member['id'] in used_ids:
                raise HTTPException(422, '分镜编号与续镜编号冲突，请重新规划')
            used_ids.add(member['id'])
            output.append(member)
    if len(output) > MAX_SCENES:
        raise HTTPException(422, '当前一次制作最多支持 80 镜，请拆分项目')
    total = sum(scene['duration'] for scene in output)
    if total > MAX_SECONDS:
        raise HTTPException(422, '当前一次制作总长不能超过 20 分钟，请拆分项目')
    updated.update(scenes=output, total_duration=total)
    return updated, adjustments
