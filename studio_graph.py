"""Mujian script/asset contracts mapped to JHT's native, editable canvas.

Definitions are not media IDs. Only authorized, completed JHT assets can satisfy
an input. Rebuilding preserves user nodes, layout, prompts and existing outputs.
"""
from copy import deepcopy
import hashlib
import json
import math
import re
import uuid
import unicodedata

from fastapi import HTTPException

MAX_NODES, MAX_EDGES, MAX_GROUP_MEMBERS, MAX_GROUPS = 1600, 10000, 400, 100


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def node_id(rid, key):
    return 'mj-' + uuid.uuid5(uuid.NAMESPACE_URL, f'{rid}:{key}').hex[:28]


def scenes_for(content, episode, *, include_appendices=False):
    lines = content.splitlines()
    matches = [(i, m.group(1)) for i, line in enumerate(lines)
               if (m := re.match(r'^#{2,5}\s*场\s*(\d+[-－—]\d+)', line))]
    if not matches:
        raise HTTPException(422, f'第 {episode} 集缺少场次标题，暂不能拆分资产')
    result = []
    for index, (start, key) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(lines)
        if not include_appendices:
            level = len(re.match(r'^#+', lines[start])[0])
            for i in range(start + 1, end):
                heading = re.match(r'^(#{1,6})\s+(.+)', lines[i])
                # Peer/parent sections end the scene; shot subheadings do not.
                if heading and len(heading[1]) <= level and not re.match(r'(?:镜(?:头)?|分镜)\s*\d+', heading[2]):
                    end = i
                    break
        if int(re.split(r'[-－—]', key)[0]) != episode:
            raise HTTPException(422, '剧本场次与集数不一致')
        result.append({'id': re.sub('[－—]', '-', key), 'line_start': start + 1,
                       'line_end': end, 'content': '\n'.join(lines[start:end])})
    if len({s['id'] for s in result}) != len(result):
        raise HTTPException(422, '剧本场次编号重复')
    return result


_SCENE_METADATA = re.compile(
    r'^(?:出场人物|人物|角色|地点|时间|场景|场次|内外景|景别|机位|镜头|镜号|时长|预计时长|合计|总时长|'
    r'统一美术|美术|视觉风格|画面风格|风格|画幅|比例|构图|色调|光影|光线|灯光|摄影|运镜|'
    r'提示词|正向提示词|负向提示词|参考图|资产|道具|连续性|动作|画面|音效|环境音|环境声|配乐|音乐|'
    r'字幕|屏幕文字|备注|说明|facts|design|anchors)$', re.I)


def scene_dialogue(content, character_names=()):
    """Read speaker lines conservatively; production labels are not spoken text."""
    names = set(character_names)
    result = []
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith(('△', '#', '|', '```')):
            continue
        line = re.sub(r'^[-*+]\s+', '', line).replace('**', '').replace('__', '')
        match = re.match(r'^([^：:]{1,24})[：:](.+)$', line)
        if not match:
            continue
        label = match[1].strip()
        speaker = re.sub(r'[（(].*?[）)]', '', label).strip()
        if speaker not in names and _SCENE_METADATA.fullmatch(speaker):
            continue
        result.append((label, match[2].strip()))
    return result


def _reference_text(value):
    """Only normalize typography, never infer an asset from a similar name."""
    if not isinstance(value, str): return ''
    return re.sub('[－—–]', '-', unicodedata.normalize('NFKC', value).strip())


def _source_reader(content, episode, scenes):
    lines = content.splitlines()
    def source(item):
        sid = _reference_text(item.get('sourceScene') or item.get('sceneId') or '')
        raw = item.get('sourceLine')
        # Accept explicit coordinates, including common model serialization.
        # Never clamp a reference into a scene or guess missing evidence.
        span = None
        if type(raw) is int:
            end = item.get('sourceLineEnd', raw)
            if type(end) is int: span = (raw, end)
        elif isinstance(raw, str):
            match = re.fullmatch(r'(?:第\s*)?(\d+)\s*(?:[-~～至]\s*(\d+))?\s*(?:行)?', _reference_text(raw))
            if match: span = (int(match[1]), int(match[2] or match[1]))
        elif isinstance(raw, list) and len(raw) in (1, 2) and all(type(n) is int for n in raw):
            span = (raw[0], raw[-1])
        scene = scenes.get(sid)
        if not scene or not span or not scene['line_start'] <= span[0] <= span[1] <= scene['line_end']:
            raise HTTPException(422, f'资产或镜头的原文场次、行号无效：场 {sid or "未指定"}，行 {raw}；请使用当前场次原文的完整行号')
        first, last = span
        return {'episode': episode, 'scene': sid, 'line': first, 'quote': '\n'.join(lines[first - 1:last]),
                **({'line_end': last} if last != first else {})}
    return source


def _resolve_asset_reference(value, kind, known, aliases):
    if not isinstance(value, str): return None
    # An existing key has authority over aliases and display names.
    if value in known:
        return value if known[value]['type'] == kind else None
    token = _reference_text(value)
    if not token: return None
    direct = [key for key in known if _reference_text(key) == token]
    if direct:
        return direct[0] if len(direct) == 1 and known[direct[0]]['type'] == kind else None
    mapped = {key for alias, keys in aliases.items() if _reference_text(alias) == token
              for key in keys if key in known and known[key]['type'] == kind}
    if mapped: return next(iter(mapped)) if len(mapped) == 1 else None
    named = [key for key, asset in known.items()
             if asset['type'] == kind and _reference_text(asset['name']) == token]
    return named[0] if len(named) == 1 else None


def _frozen_asset_reference(raw, known, aliases):
    """Validate a usage summary against registered identities, without new evidence."""
    summary = {'key': raw} if isinstance(raw, str) else raw
    fields = {'key', 'reuseId', 'type', 'name', 'variant', 'usedInShots'}
    if not isinstance(summary, dict) or not set(summary).issubset(fields):
        return False
    token = summary.get('key') or summary.get('reuseId')
    if not isinstance(token, str) or not token.strip():
        return False
    matches = {key for kind in ('character', 'scene', 'prop')
               if (key := _resolve_asset_reference(token, kind, known, aliases))}
    if len(matches) != 1:
        raise HTTPException(422, f'当前资产目录尚未定义或不能唯一识别「{token[:100]}」；请先登记资产，再拆解此场次')
    key = next(iter(matches))
    existing = known[key]
    for field in ('type', 'name', 'variant'):
        if field in summary and (not isinstance(summary[field], str)
                or _reference_text(summary[field]) != _reference_text(existing.get(field, ''))):
            raise HTTPException(422, f'资产使用清单「{key}」的{field}与已登记素材不一致；请沿用原有身份与造型，不得在拆镜时更改')
    if summary.get('reuseId') and _resolve_asset_reference(summary['reuseId'], existing['type'], known, aliases) != key:
        raise HTTPException(422, f'资产使用清单「{key}」的 reuseId 与已登记素材不一致')
    return True


def _merge_asset_catalog(assets, catalog, source, episode, *, frozen=False):
    if not isinstance(assets, list) or len(assets) > 80:
        raise HTTPException(422, '需返回最多 80 项资产定义列表')
    known = {a['key']: deepcopy(a) for a in catalog}
    aliases = {}
    for key, item in known.items():
        for alias in item.get('aliases', []):
            if isinstance(alias, str): aliases.setdefault(alias, set()).add(key)
    seen = set()
    for raw in assets:
        # Scene workers sometimes echo catalog keys or usage summaries. They
        # reference frozen definitions, so they need no second source location.
        # Full definitions still undergo the original source validation below.
        if frozen and _frozen_asset_reference(raw, known, aliases):
            continue
        if not isinstance(raw, dict):
            raise HTTPException(422, '资产条目格式无效')
        supplied_key = _reference_text(raw.get('key') or raw.get('reuseId'))
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', supplied_key) or supplied_key in seen:
            raise HTTPException(422, '资产编号重复或无效')
        seen.add(supplied_key)
        if raw.get('type') not in ('character', 'scene', 'prop') or not isinstance(raw.get('name'), str) or not raw['name'].strip():
            raise HTTPException(422, '资产名称或类型无效')
        for field in ('facts', 'design', 'variant'):
            if not isinstance(raw.get(field, ''), str) or len(raw.get(field, '')) > 2500:
                raise HTTPException(422, '资产事实或设计描述格式无效或过长')
        if not isinstance(raw.get('anchors', []), list) or len(raw.get('anchors', [])) > 30 or any(not isinstance(a, str) or len(a) > 500 for a in raw.get('anchors', [])):
            raise HTTPException(422, '资产识别特征格式无效')
        evidence = source(raw)
        identity = (raw['type'], _reference_text(raw['name']), _reference_text(raw.get('variant', '')))
        key = supplied_key if supplied_key in known else None
        base_key = None
        if raw.get('reuseId'):
            reused_key = _resolve_asset_reference(raw['reuseId'], raw['type'], known, aliases)
            if not reused_key:
                raise HTTPException(422, f'资产「{raw["name"]}」的 reuseId {str(raw["reuseId"])[:100]} 未定义或不唯一；请使用目录中同身份的编号')
            reused = known[reused_key]
            if _reference_text(reused['name']) != identity[1]:
                raise HTTPException(422, '复用资产必须保持原有编号、名称与身份；不同造型请使用新编号')
            if 'variant' in raw and _reference_text(reused.get('variant', '')) != identity[2]:
                # A changed costume/state is a new definition, not an alias for
                # the old look. Keep earlier episodes' visual identity intact.
                if supplied_key == reused_key:
                    raise HTTPException(422, '不同造型请使用新编号；不能覆盖已有资产变体')
                base_key = reused_key
            else:
                if key and key != reused_key:
                    raise HTTPException(422, '资产别名与已有编号冲突，请沿用对应资产的稳定编号')
                key = reused_key
        if key:
            existing = known[key]
            if (existing['type'] != raw['type'] or _reference_text(existing['name']) != identity[1]
                    or ('variant' in raw and _reference_text(existing.get('variant', '')) != identity[2])):
                raise HTTPException(422, '复用资产必须保持原有编号、名称与身份；不同造型请使用新编号')
            if supplied_key in known and supplied_key != key:
                raise HTTPException(422, '资产别名与已有编号冲突，请沿用对应资产的稳定编号')
        else:
            matches = [k for k, a in known.items()
                       if (a['type'], _reference_text(a['name']), _reference_text(a.get('variant', ''))) == identity]
            if len(matches) > 1:
                raise HTTPException(422, f'资产「{raw["name"]}」存在多个同名定义，请明确引用原有编号')
            key = matches[0] if matches else None
        if not key:
            if frozen:
                raise HTTPException(422, f'当前资产目录尚未定义「{raw["name"]}」（{supplied_key}）；请先登记资产，再拆解此场次，不能临时编造引用')
            key = supplied_key
            item = {k: raw.get(k, '') for k in ('type', 'name', 'variant', 'facts', 'design')}
            item.update(key=key, sources=[], episodes=[], anchors=deepcopy(raw.get('anchors', [])))
            if base_key: item['baseKey'] = base_key
            known[key] = item
        item = known[key]
        if not frozen:
            if evidence not in item.get('sources', []):
                item['sources'] = [*item.get('sources', []), evidence]
            item['episodes'] = sorted(set(item.get('episodes', []) + [episode]))
            # Existing identity/design is the shared contract for all scene workers.
            # Registration only fills missing data; revisions need a separate edit.
            for field in ('facts', 'design', 'anchors'):
                if not item.get(field) and raw.get(field): item[field] = deepcopy(raw[field])
        # Persist only definition identifiers. A display name used as reuseId
        # may become ambiguous when another costume variant is registered.
        for alias in (raw.get('key'), supplied_key):
            if isinstance(alias, str) and alias and alias != key:
                aliases.setdefault(alias, set()).add(key)
                if not frozen:
                    item['aliases'] = list(dict.fromkeys([*item.get('aliases', []), alias]))
    return known, aliases


def validate_asset_catalog(value, content, episode, catalog):
    """Register reusable identities before independent scene extraction starts."""
    if not isinstance(value, dict): raise HTTPException(422, '资产登记需返回 JSON 对象')
    scenes = {s['id']: s for s in scenes_for(content, episode)}
    known, aliases = _merge_asset_catalog(value.get('assets'), catalog,
                                         _source_reader(content, episode, scenes), episode)
    return {'assets': list(known.values()),
            'aliases': {alias: next(iter(keys)) for alias, keys in aliases.items() if len(keys) == 1},
            'warnings': value.get('warnings', [])}


def validate_extraction(value, content, episode, catalog, *, scene_ids=None, frozen_catalog=False):
    """Validate source coverage and references using complete-script line numbers."""
    scenes = {s['id']: s for s in scenes_for(content, episode)}
    if scene_ids is not None:
        selected = set(scene_ids)
        if not selected or not selected.issubset(scenes):
            raise HTTPException(422, '指定的拆解场次不存在或为空')
        scenes = {key: scene for key, scene in scenes.items() if key in selected}
    if not isinstance(value, dict): raise HTTPException(422, '镜头拆解需返回 JSON 对象')
    assets, shots = value.get('assets'), value.get('shots')
    if not isinstance(assets, list) or not isinstance(shots, list) or not 1 <= len(shots) <= 80 or len(assets) > 80:
        raise HTTPException(422, f'第 {episode} 集需返回资产列表和 1–80 个完整镜头')
    source = _source_reader(content, episode, scenes)
    known, aliases = _merge_asset_catalog(assets, catalog, source, episode, frozen=frozen_catalog)
    covered = set()
    out = []
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise HTTPException(422, '镜头格式无效')
        shot = deepcopy(shot)
        dialogue = shot.get('dialogue', '')
        if dialogue is None:
            shot['dialogue'] = ''
        elif isinstance(dialogue, list):
            spoken = []
            for entry in dialogue:
                if isinstance(entry, str):
                    spoken.append(entry)
                elif (isinstance(entry, dict) and isinstance(entry.get('text'), str)
                      and isinstance(entry.get('speaker', ''), str)
                      and type(entry.get('offscreen', False)) is bool):
                    speaker = entry.get('speaker', '') + ('（画外音）' if entry.get('offscreen') else '')
                    spoken.append((speaker + '：' if speaker else '') + entry['text'])
                else:
                    raise HTTPException(422, '镜头对白格式无效，请保留说话人与完整台词')
            shot['dialogueSegments'] = dialogue
            shot['dialogue'] = '\n'.join(spoken)
        evidence = source(shot)
        covered.add(evidence['scene'])
        seconds = shot.get('seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 60:
            raise HTTPException(422, '单镜时长须为 1–60 秒')
        chars, props = shot.get('characterKeys', []), shot.get('propKeys', [])
        if not isinstance(chars, list) or not isinstance(props, list):
            raise HTTPException(422, '镜头引用列表格式无效')
        raw_refs = [(shot.get('sceneKey'), 'scene', 'sceneKey'),
                    *[(k, 'character', 'characterKeys') for k in chars],
                    *[(k, 'prop', 'propKeys') for k in props]]
        refs, errors = [], []
        for raw_key, kind, field in raw_refs:
            key = _resolve_asset_reference(raw_key, kind, known, aliases)
            if key is None:
                candidates = [f'{k}={a["name"]}' + (f'（{a["variant"]}）' if a.get('variant') else '')
                              for k, a in known.items() if a['type'] == kind]
                errors.append(f'{field}={str(raw_key)[:120]}；可用 {kind} 编号：' + ('、'.join(candidates[:12]) or '无'))
            else:
                refs.append((key, kind))
        if errors:
            raise HTTPException(422, f'镜头引用了尚未定义、类型不符或不唯一的角色、场景或道具：场 {evidence["scene"]}，第 {index + 1} 镜（原文行 {evidence["line"]}）；' + '；'.join(errors)
                                + '。请只修正这些引用；未登记资产需先补充定义，不能删除原有镜头或对白。')
        if len({key for key, _ in refs}) != len(refs) or len(refs) > 9:
            raise HTTPException(422, '镜头参考重复或超过 9 项，请拆镜')
        for field in ('title', 'framing', 'staging', 'startState', 'action', 'endState', 'camera', 'lighting', 'sound', 'dialogue', 'continuity'):
            if not isinstance(shot.get(field, ''), str) or len(shot.get(field, '')) > 2000:
                raise HTTPException(422, '镜头文字字段格式无效或过长')
        if not shot.get('action') or not shot.get('startState') or not shot.get('endState'):
            raise HTTPException(422, '镜头必须包含起始状态、动作和结束状态')
        out.append({**shot, 'key': f'ep{episode}-shot{index + 1}', 'episode': episode, 'sceneId': evidence['scene'], 'sourceLine': evidence['line'],
                    **({'sourceLineEnd': evidence['line_end']} if 'line_end' in evidence else {}),
                    'sceneKey': refs[0][0], 'characterKeys': [key for key, kind in refs if kind == 'character'],
                    'propKeys': [key for key, kind in refs if kind == 'prop'],
                    'seconds': round(seconds, 2), 'references': [k for k, _ in refs], 'sources': [evidence]})
    if covered != set(scenes):
        raise HTTPException(422, '镜头未覆盖全部原文场次：' + '、'.join(set(scenes) - covered))
    def dialogue_text(text):
        return ''.join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith('P'))
    for sid, scene in scenes.items():
        required = scene_dialogue(scene['content'], [a['name'] for a in known.values() if a['type'] == 'character'])
        speakers = {re.sub(r'[（(].*?[）)]', '', label).strip() for label, _ in required}
        spoken_lines = []
        for shot in out:
            if shot['sceneId'] != sid: continue
            for line in shot.get('dialogue', '').splitlines():
                match = re.match(r'^([^：:]{1,24})[：:](.+)$', line.strip())
                speaker = re.sub(r'[（(].*?[）)]', '', match[1]).strip() if match else ''
                spoken_lines.append(match[2] if speaker in speakers else line)
        spoken = dialogue_text('\n'.join(spoken_lines))
        for _, text in required:
            if dialogue_text(text) not in spoken:
                raise HTTPException(422, f'场 {sid} 的原文对白遗漏或被改写：{text[:80]}')
    return {'assets': list(known.values()), 'shots': out, 'warnings': value.get('warnings', [])}


def plan_video_clips(shots, capabilities=None, reference_limit=9, asset_catalog=None):
    """Pack consecutive shots in one scene without losing their individual timing."""
    caps = capabilities or {}
    durations = sorted({int(n) for n in caps.get('durations', [])
                        if isinstance(n, (int, float)) and not isinstance(n, bool) and n == int(n) and 1 <= n <= 60})
    # Missing capability data is not permission to assume a longer generation.
    maximum = max(durations) if durations else None
    multi_shot = caps.get('multi_shot') is True
    catalog = {a['key']: a for a in (asset_catalog or [])}
    def variant_conflict(refs):
        identities = {}
        for key in refs:
            asset = catalog.get(key)
            if not asset or asset['type'] not in ('character', 'scene'): continue
            identity = (asset['type'], _reference_text(asset['name']))
            variant = _reference_text(asset.get('variant', ''))
            if identity in identities and identities[identity] != variant: return True
            identities[identity] = variant
        return False
    result, pending = [], []
    def finish():
        if not pending: return
        first, last = pending[0], pending[-1]
        seconds = round(sum(s['seconds'] for s in pending), 2)
        duration = next((n for n in durations if n >= seconds), math.ceil(seconds))
        refs = list(dict.fromkeys(k for s in pending for k in s['references']))
        clip = deepcopy(first)
        clip.update(seconds=duration, plannedSeconds=seconds, sourceShots=deepcopy(pending),
                    references=refs, endState=last['endState'],
                    sources=[source for s in pending for source in s['sources']],
                    dialogue='\n'.join(s.get('dialogue', '') for s in pending if s.get('dialogue')))
        if len(pending) > 1:
            clip['key'] = first['key'] + '-to-' + last['key']
            clip['title'] = first.get('title') or '连续分镜'
        result.append(clip); pending.clear()
    for shot in shots:
        if len(set(shot['references'])) > reference_limit:
            raise HTTPException(422, f'「{shot.get("title", "分镜")}」参考素材超过 {reference_limit} 项，请调整引用或生成模式')
        if maximum and shot['seconds'] > maximum:
            raise HTTPException(422, f'「{shot.get("title", "分镜") }」超过所选模型单次 {maximum} 秒上限，请拆分动作或对白后重新规划')
        refs = set(k for s in [*pending, shot] for k in s['references'])
        same_scene = not pending or (shot['sceneKey'], shot['sceneId']) == (pending[0]['sceneKey'], pending[0]['sceneId'])
        # Leave room for style and reference tokens in the 4000-character prompt.
        text_size = sum(len(str(s.get(k, ''))) for s in [*pending, shot]
                        for k in ('framing', 'staging', 'startState', 'action', 'endState', 'camera', 'lighting', 'sound', 'dialogue', 'continuity'))
        if pending and (not maximum or not same_scene or shot.get('generationBreakBefore') is True
                        or (shot.get('cutBefore') is True and not multi_shot) or variant_conflict(refs)
                        or round(sum(s['seconds'] for s in pending) + shot['seconds'], 2) > maximum
                        or len(refs) > reference_limit or text_size > 2200):
            finish()
        pending.append(shot)
    finish()
    return result


def clip_storyboard(clip):
    parts, elapsed = [], 0
    for index, shot in enumerate(clip['sourceShots'], 1):
        end = round(elapsed + shot['seconds'], 2)
        transition = '按指定时间切换至下一机位，保持动作和对白衔接。\n' if index > 1 else ''
        parts.append(f"分镜 {index}（{elapsed:g}–{end:g} 秒）：{shot['framing']}；{shot['staging']}；{shot['lighting']}\n" + transition +
                     '\n'.join(f'{label}：{shot.get(field, "")}' for label, field in
                     [('起始画面', 'startState'), ('动作', 'action'), ('结束画面', 'endState'), ('运镜', 'camera'), ('声音', 'sound'), ('原文对白', 'dialogue'), ('连续性', 'continuity')]))
        elapsed = end
    if clip['seconds'] > elapsed:
        parts.append(f"{elapsed:g}–{clip['seconds']:g} 秒：保持末镜结束状态，自然收尾，不增加台词或剧情。")
    return '\n\n'.join(parts)


def add_prompt_mentions(graph):
    nodes = {n['id']: n for n in graph['nodes']}
    for node in graph['nodes']:
        settings = node.get('image_settings') or (node.get('settings') if node['type'] == 'video' else None)
        if not settings: continue
        mentions, tokens = [], set()
        for index, ref in enumerate(node['mujian']['inputs'], 1):
            source = nodes[ref['nodeId']]
            token = '@' + source['title'].replace('@', '').replace('\n', ' ')
            base, suffix = token, 2
            while token in tokens:
                token = base + '（' + str(suffix) + '）'; suffix += 1
            tokens.add(token)
            mentions.append({'token': token, 'node_id': source['id'], 'asset_id': '', 'name': source['title'], 'kind': ref['kind']})
        settings['prompt'] = re.sub(r'图片\s*(\d+)', lambda m: mentions[int(m[1])-1]['token'] if 0 < int(m[1]) <= len(mentions) else m[0], settings['prompt'])
        absent = [m['token'] for m in mentions if m['token'] not in settings['prompt']]
        if absent: settings['prompt'] = '参考：' + '、'.join(absent) + '\n' + settings['prompt']
        settings['mentions'] = mentions
        if len(settings['prompt']) > 4000:
            raise HTTPException(422, f'「{node["title"]}」提示词超过 4000 字，请精简描述或拆分片段')


def layout_studio_graph(graph, rid, catalog):
    """Asset families on the left, scene studies in the middle, episodes on the right."""
    families, scenes, episodes = {}, {}, {}
    for node in graph['nodes']:
        meta = node['mujian']; role = meta['role']
        if role == 'reference':
            asset = catalog[meta['definitionId']]
            if asset['type'] == 'scene':
                scenes.setdefault(asset['name'], []).append(node)
            else:
                name = ('人物 · ' + asset['name']) if asset['type'] == 'character' else '道具参考'
                families.setdefault(name, []).append(node)
        elif role in ('firstFrame', 'lastFrame'):
            scenes.setdefault(catalog[meta['sceneKey']]['name'], []).append(node)
        elif meta.get('episode'):
            episodes.setdefault(meta['episode'], []).append(node)
        elif node['type'] == 'audio':
            families.setdefault('声音素材', []).append(node)
    graph['groups'] = []
    step_x, gap_y = 368, 32
    def place(key, title, nodes, x, y, columns, color):
        # Match native node cards and reserve portrait height before media arrives.
        rows = [nodes[i:i + columns] for i in range(0, len(nodes), columns)]
        row_y = y + 40
        for row in rows:
            heights = []
            for node in row:
                settings = node.get('image_settings') or node.get('settings') or {}
                ratio = settings.get('aspect_ratio', '16:9').split(':')
                portrait = len(ratio) == 2 and float(ratio[0]) < float(ratio[1])
                heights.append(334 if portrait else 230)
            for col, node in enumerate(row):
                node['x'] = x + 24 + col * step_x
                node['y'] = row_y
            row_y += max(heights) + gap_y
        height = row_y - y - gap_y + 24
        graph['groups'].append({'id': node_id(rid, key), 'title': title[:80], 'color': color,
                                'node_ids': [n['id'] for n in nodes], 'x': x, 'y': y,
                                'width': (min(columns, len(nodes)) - 1) * step_x + 384, 'height': height})
        return height
    scene_x = 80 + max((min(2, len(nodes)) * step_x + 124 for nodes in families.values()), default=0)
    episode_x = scene_x + max((min(3, len(nodes)) * step_x + 164 for nodes in scenes.values()), default=0)
    final_x = episode_x + 1000
    y = 80
    for title, nodes in families.items():
        y += place('family:' + title, title, nodes, 80, y, 2, '#999999') + 100
    y = 80
    for title, nodes in scenes.items():
        y += place('scene:' + title, '场景 · ' + title, nodes, scene_x, y, 3, '#15b9d0') + 100
    y = 450
    for ep, nodes in sorted(episodes.items()):
        # Script and assembly sit next to the episode's clip sequence.
        videos = [n for n in nodes if n['type'] == 'video']
        script = next(n for n in nodes if n['mujian']['role'] == 'script')
        edit = next(n for n in nodes if n['mujian']['role'] == 'edit')
        columns = min(4, len(videos))
        height = place(f'group:{ep}', f'第 {ep} 集 · 视频片段', videos, episode_x, y, columns, '#9061f9')
        script.update(x=episode_x, y=y - 350)
        edit.update(x=episode_x + columns * step_x + 124, y=y + 40)
        final_x = max(final_x, edit['x'] + 640)
        y += max(height, 450) + 450
    final = next(n for n in graph['nodes'] if n['mujian']['role'] == 'final')
    final.update(x=final_x, y=134)
    graph['viewport'] = {'x': 40, 'y': 60, 'zoom': round(max(.05, min(.6, 1400 / max(n['x'] + 440 for n in graph['nodes']), 780 / max(n['y'] + 500 for n in graph['nodes']))), 3)}


def build_graph(run, old=None, capabilities=None):
    studio = run['studio']
    ratio = run['preferences']['aspect_ratio']
    # Automated Studio production uses the registered materials directly.
    # Legacy first/last-frame settings still work on native manual nodes, but
    # must not recreate an extra rendering stage when an old run is synced.
    mode = 'reference'
    style = studio.get('style_bible') or run['preferences'].get('style', '')
    if isinstance(style, dict):
        style = '\n'.join(f'{k}：{v}' for k, v in style.items())
    graph = {'name': run['title'], 'nodes': [], 'edges': [], 'groups': [], 'selected_asset_ids': [],
             'viewport': {'x': 40, 'y': 60, 'zoom': .6}}
    catalog = {a['key']: a for a in studio.get('catalog', [])}
    capabilities = capabilities or studio.get('video_capabilities', {})
    def add(key, kind, title, role, x, y, inputs=(), **extra):
        nid = node_id(run['id'], key)
        metadata = extra.pop('metadata', {})
        node = {'id': nid, 'type': kind, 'title': title[:160], 'x': x, 'y': y, **extra,
                'mujian': {'runId': run['id'], 'role': role, 'key': key, 'inputs': list(inputs), **metadata}}
        graph['nodes'].append(node)
        for order, ref in enumerate(inputs):
            graph['edges'].append({'from': ref['nodeId'], 'to': nid, 'relation': 'edit-input' if ref['kind'] == 'video' else 'reference',
                                   'order': order + 1, 'slot': ref.get('slot', ''), 'role': ref['role'], 'mujian': True, 'studio_run_id': run['id'],
                                   'hidden': kind in ('video', 'timeline')})
        return node
    def ref(key, kind='image', role='身份与环境参考', slot=''):
        return {'nodeId': node_id(run['id'], key), 'kind': kind, 'role': role, 'slot': slot}
    def image_settings(prompt, aspect=ratio):
        if not 0 < len(prompt) <= 4000:
            raise HTTPException(422, '图片提示词超过 4000 字，请精简资产描述后重建画布')
        return {'prompt': prompt, 'aspect_ratio': aspect, 'size': '2K', 'count': 1, 'watermark': False,
                'model_config_id': run['preferences']['models'].get('image', ''), 'reference_asset_ids': [],
                'visual_style': run['preferences'].get('visual_style')}
    family_roots = {}
    for i, asset in enumerate(catalog.values()):
        prompt = f"{asset['name']}。{asset.get('facts', '')}\n美术设计：{asset.get('design', '')}\n稳定特征：{'；'.join(asset.get('anchors', []))}\n统一风格：{style}"
        title = asset['name'] + (' · ' + asset['variant'] if asset.get('variant') and asset['variant'] != '默认' else '')
        family = (asset['type'], asset['name'])
        root = family_roots.setdefault(family, asset['key'])
        inputs = [ref('asset:' + root, role='保持人物身份与基础设计' if asset['type'] == 'character' else '保持场景空间与基础设计')] if root != asset['key'] and asset['type'] in ('character', 'scene') else []
        if inputs: prompt = '参考图片1保持基础设计一致，按以下变体调整：\n' + prompt
        add('asset:' + asset['key'], 'prop' if asset['type'] == 'prop' else 'asset', title, 'reference',
            80 + i % 3 * 440, 80 + i // 3 * 400, inputs, image_settings=image_settings(prompt, '3:4' if asset['type'] == 'character' else ratio),
            metadata={'definitionId': asset['key'], 'assetType': asset['type'], 'sources': asset['sources']})
    group_y = 80
    for ep in range(1, studio['brief']['episode_count'] + 1):
        doc = studio['documents'].get(f'episode-{ep}')
        pack = studio.get('extractions', {}).get(str(ep))
        if not doc or doc['status'] != 'current' or not pack or pack['source_digest'] != digest(doc['content']):
            raise HTTPException(409, f'第 {ep} 集剧本或资产待同步')
        members = []
        script = add(f'script:{ep}', 'prompt', f'第 {ep} 集 · 剧本', 'script', 1500, group_y,
                     settings={'prompt': doc['content']}, metadata={'episode': ep, 'artifactVersion': doc['version']})
        members.append(script['id'])
        videos = []
        for i, shot in enumerate(plan_video_clips(pack['shots'], capabilities,
                                min(9, capabilities.get('max_images') or capabilities.get('max_reference_images') or 9), list(catalog.values()))):
            key = 'shot:' + shot['key']
            y = group_y + 350 + i * 370
            refs = [ref('asset:' + k, role=catalog[k]['name']) for k in shot['references']]
            ref_text = '\n'.join(f"图片{j + 1}：{r['role']}。" for j, r in enumerate(refs))
            inputs = refs
            prompt = ref_text
            prompt += f"\n本片段包含 {len(shot['sourceShots'])} 个分镜，按以下顺序与时长完成。\n{clip_storyboard(shot)}\n视觉风格：{style}"
            if len(prompt) > 4000:
                raise HTTPException(422, f'第 {ep} 集第 {i+1} 镜提示词超过 4000 字，请调整拆解')
            video = add(key, 'video', f"第 {ep} 集 · 片段 {i+1:02d} · {shot.get('title') or '镜头'}", 'shot', 2380, y, inputs,
                        settings={'prompt': prompt, 'negative_prompt': '', 'video_mode': 'auto',
                                  'aspect_ratio': ratio, 'duration': shot['seconds'], 'resolution': studio.get('resolution', '768p'),
                                  'model_config_id': run['preferences']['models'].get('video', ''), 'asset_ids': [], 'reference_order': [],
                                  'visual_style': run['preferences'].get('visual_style')},
                        metadata={'mode': mode, 'episode': ep, 'index': i + 1, 'sceneKey': shot['sceneKey'],
                                  'shots': shot['sourceShots'], 'plannedSeconds': shot['plannedSeconds'],
                                  'sources': shot['sources'], 'dialogue': shot.get('dialogue', '')})
            members.append(video['id']); videos.append(video)
        edit = add(f'edit:{ep}', 'timeline', f'第 {ep} 集 · 剪辑', 'edit', 2820, group_y + 350,
                   [ref(v['mujian']['key'], 'video', '按镜头顺序剪辑') for v in videos], aspect_ratio=ratio,
                   clips=[{'nodeId': v['id'], 'duration': v['settings']['duration'], 'muteOriginal': False} for v in videos],
                   metadata={'episode': ep})
        members.append(edit['id'])
        graph['groups'].append({'id': node_id(run['id'], f'group:{ep}'), 'title': f'第 {ep} 集',
                                'color': ['#9061f9', '#4285f4', '#15b9d0'][ep % 3], 'node_ids': members})
        group_y += 550 + len(videos) * 370
    edits = [n for n in graph['nodes'] if n['mujian']['role'] == 'edit']
    add('final', 'timeline', '整剧 · 成片合成', 'final', 3380, 80,
        [ref(n['mujian']['key'], 'video', '按集数合成') for n in edits], aspect_ratio=ratio, assembly='concat-episodes',
        clips=[{'nodeId': n['id'], 'duration': sum(c['duration'] for c in n['clips']), 'muteOriginal': False} for n in edits])
    add_prompt_mentions(graph)
    layout_studio_graph(graph, run['id'], catalog)
    return merge_graph(graph, old, run['id'])


_DEFINITION_FIELDS = ('settings', 'image_settings', 'clips', 'aspect_ratio')
_GENERATION_META = ('inputs', 'mode', 'shots', 'plannedSeconds', 'sources', 'sceneKey', 'episode', 'index', 'dialogue')


def _definition_hash(node):
    meta = node.get('mujian', {})
    return digest([{key: node.get(key) for key in _DEFINITION_FIELDS}, meta.get('inputs', []), meta.get('sources')])


def generation_proposal(node, graph=None):
    """Keep a replacement prompt and its reference contract in one proposal."""
    meta = node.get('mujian', {})
    fingerprint = _definition_hash(node)
    result = {key: deepcopy(node.get(key)) for key in _DEFINITION_FIELDS}
    result['mujian'] = {key: deepcopy(meta[key]) for key in _GENERATION_META if key in meta}
    result['mujian'].update(definitionHash=fingerprint, appliedDefinitionHash=fingerprint)
    if graph is not None:
        result['incomingEdges'] = [deepcopy(edge) for edge in graph.get('edges', [])
                                   if edge['to'] == node['id'] and edge.get('studio_run_id') == meta.get('runId')]
    return result


def _has_generation_output(node):
    return any(node.get(key) for key in ('assetId', 'assetUrl', 'referenceAssetId', 'referenceTaskId', 'taskId', 'image_task_id'))


def _strip_canvas_defaults(node):
    """Undo only defaults added by public/app.js normaliseNodes on save."""
    settings = node.get('settings')
    if not isinstance(settings, dict): return
    defaults = {'prompt': '', 'negative_prompt': '模糊、抖动、人物变形、字幕、水印', 'mode': 'text-to-video',
                'aspect_ratio': '16:9', 'duration': 4, 'resolution': '768p', 'asset_ids': []}
    if node.get('type') == 'video':
        # Studio video settings always supplied the other fields itself.
        if settings.get('mode') == defaults['mode']: settings.pop('mode')
        return
    retained = {'prompt'} if node.get('mujian', {}).get('role') == 'script' else set()
    extra = set(settings) - retained
    if not extra.issubset(set(defaults) | {'model_config_id'}): return
    if any(settings[key] != defaults[key] for key in extra if key != 'model_config_id'): return
    # model_config_id belongs to the injected *video* settings here. Images
    # use image_settings; a script/timeline does not generate video from it.
    if 'model_config_id' in settings and not isinstance(settings['model_config_id'], str): return
    if retained:
        node['settings'] = {key: settings[key] for key in retained if key in settings}
    else:
        node.pop('settings', None)


def _unchanged_generated_node(node, graph):
    """A saved generation hash is proof; absence of a baseline is not proof."""
    meta = node.get('mujian', {})
    baseline = meta.get('appliedDefinitionHash') or meta.get('definitionHash')
    if not baseline or meta.get('needsReview') or meta.get('proposed'):
        return False
    expected_edges = [(ref['nodeId'], ref.get('slot', '')) for ref in meta.get('inputs', [])]
    actual_edges = [(edge['from'], edge.get('slot', '')) for edge in graph.get('edges', [])
                    if edge['to'] == node['id'] and edge.get('relation') != 'agent-flow']
    if sorted(expected_edges) != sorted(actual_edges):
        return False
    if _definition_hash(node) == baseline:
        return True
    normalized = deepcopy(node)
    nodes = {item['id']: item for item in graph.get('nodes', [])}
    inputs = meta.get('inputs', [])
    resolved = [(nodes.get(ref['nodeId']) or {}).get('assetId') for ref in inputs]
    for field, reference_field in (('settings', 'asset_ids'), ('image_settings', 'reference_asset_ids')):
        settings = normalized.get(field)
        if not isinstance(settings, dict): continue
        settings.pop('last_result_asset_id', None)
        if resolved and all(resolved) and settings.get(reference_field) == resolved:
            settings[reference_field] = []
        slots = [(nodes.get(next((ref['nodeId'] for ref in inputs if ref.get('slot') == slot), '')) or {}).get('assetId')
                 for slot in ('first', 'last')]
        if settings.get('frame_asset_ids') == slots:
            settings.pop('frame_asset_ids', None)
    if _definition_hash(normalized) == baseline:
        return True
    _strip_canvas_defaults(normalized)
    return _definition_hash(normalized) == baseline


def merge_graph(fresh, old, rid):
    old = old or {'nodes': [], 'edges': [], 'groups': []}
    previous = {n['id']: n for n in old['nodes']}
    changed, preserved_inputs = set(), set()
    for node in fresh['nodes']:
        meta = node['mujian']
        meta['definitionHash'] = _definition_hash(node)
        meta['appliedDefinitionHash'] = meta['definitionHash']
        proposal = generation_proposal(node, fresh)
        saved = previous.get(node['id'])
        if not saved:
            continue
        saved_meta = saved.get('mujian', {})
        same = saved_meta.get('definitionHash') == meta['definitionHash']
        legacy_video = (node['type'] == 'video' and meta.get('mode') == 'reference'
                        and (saved_meta.get('mode') in ('first', 'first-last', 'text')
                             or any(previous.get(ref['nodeId'], {}).get('mujian', {}).get('role') in ('firstFrame', 'lastFrame')
                                    for ref in saved_meta.get('inputs', []))))
        user_inputs = any(edge['to'] == node['id'] and edge.get('studio_run_id') != rid
                          and edge.get('relation') != 'agent-flow' for edge in old.get('edges', []))
        automatic_migration = (legacy_video and not _has_generation_output(saved) and not user_inputs
                               and _unchanged_generated_node(saved, old))
        if automatic_migration:
            for key in ('x', 'y', 'title'):
                if key in saved: node[key] = deepcopy(saved[key])
            changed.add(node['id'])
            continue
        # Native editors are the source of truth for a user's own prompt/model/layout.
        for key in ('x', 'y', 'title', 'assetId', 'assetUrl', 'referenceAssetId', 'referenceTaskId', 'taskId', 'image_task_id', 'mediaWidth', 'mediaHeight', 'settings', 'image_settings', 'clips', 'aspect_ratio'):
            if key in saved:
                node[key] = deepcopy(saved[key])
        # Preserving a prompt must preserve its actual inputs and shot metadata,
        # otherwise an old prompt silently starts consuming a different image.
        for key in _GENERATION_META:
            if key in saved_meta: meta[key] = deepcopy(saved_meta[key])
            else: meta.pop(key, None)
        meta['appliedDefinitionHash'] = saved_meta.get('appliedDefinitionHash') or saved_meta.get('definitionHash')
        preserved_inputs.add(node['id'])
        if not same:
            meta.update(needsReview=True, proposed=proposal)
            changed.add(node['id'])
            if node.get('assetId'):
                meta['mediaStale'] = True
        else:
            for key in ('needsReview', 'mediaStale', 'proposed', 'previousMedia'):
                if key in saved.get('mujian', {}): meta[key] = deepcopy(saved['mujian'][key])
    ids = {n['id'] for n in fresh['nodes']}
    # A protected old video (or a user's own node) keeps its frame dependencies
    # until its replacement proposal is explicitly accepted.
    required = set(preserved_inputs)
    for saved in old['nodes']:
        if saved['id'] not in ids and (saved.get('mujian', {}).get('runId') != rid
                                      or (_has_generation_output(saved) and saved.get('mujian', {}).get('role') not in ('firstFrame', 'lastFrame'))):
            required.add(saved['id'])
    pending = list(required)
    effective = {node['id']: node for node in fresh['nodes']}
    while pending:
        nid = pending.pop()
        use_fresh = nid in effective and nid not in preserved_inputs
        current = effective[nid] if use_fresh else previous.get(nid, {})
        dependencies = [ref['nodeId'] for ref in current.get('mujian', {}).get('inputs', [])]
        dependencies += [edge['from'] for edge in (fresh if use_fresh else old).get('edges', [])
                         if edge['to'] == nid and edge.get('relation') != 'agent-flow']
        for dependency in dependencies:
            if dependency not in required:
                required.add(dependency); pending.append(dependency)
    # Removed definitions remain inspectable, but can no longer be used silently.
    for node in old['nodes']:
        if node['id'] in ids: continue
        saved = deepcopy(node)
        if saved.get('mujian', {}).get('runId') == rid:
            is_frame = saved['mujian'].get('role') in ('firstFrame', 'lastFrame')
            if is_frame:
                standard_title = bool(re.fullmatch(r'第 \d+ 集片段 \d{2,} · (?:首帧|尾帧)', saved.get('title', '')))
                if (saved['id'] not in required and standard_title and not _has_generation_output(saved)
                        and _unchanged_generated_node(saved, old)):
                    continue
                if saved['id'] not in required:
                    saved['mujian'].update(archived=True)
            else:
                saved['mujian'].update(needsReview=True, archived=True)
        fresh['nodes'].append(saved)
        preserved_inputs.add(saved['id'])
    ids = {n['id'] for n in fresh['nodes']}
    fresh['edges'] = [edge for edge in fresh['edges'] if edge['to'] not in preserved_inputs or edge.get('studio_run_id') != rid]
    existing_edges = {(e['from'], e['to'], e.get('slot', '')) for e in fresh['edges']}
    for edge in old['edges']:
        if ((edge.get('studio_run_id') != rid or edge['to'] in preserved_inputs)
                and edge['from'] in ids and edge['to'] in ids and (edge['from'], edge['to'], edge.get('slot', '')) not in existing_edges):
            fresh['edges'].append(deepcopy(edge))
            existing_edges.add((edge['from'], edge['to'], edge.get('slot', '')))
    own_groups = {g['id'] for g in fresh['groups']}
    user_groups = [deepcopy(g) for g in old.get('groups', []) if g['id'] not in own_groups]
    for group in user_groups:
        group['node_ids'] = [nid for nid in group['node_ids'] if nid in ids]
    user_groups = [group for group in user_groups if group['node_ids']]
    user_members = {nid for g in user_groups for nid in g['node_ids']}
    for group in fresh['groups']:
        group['node_ids'] = [nid for nid in group['node_ids'] if nid not in user_members]
        saved = next((g for g in old.get('groups', []) if g['id'] == group['id']), None)
        if saved: group.update({k: saved[k] for k in ('title', 'color') if k in saved})
    fresh['groups'] = [g for g in fresh['groups'] if g['node_ids']] + user_groups
    fresh['viewport'] = old.get('viewport') or fresh['viewport']
    if old.get('folder_id'): fresh['folder_id'] = old['folder_id']
    if 'revision' in old: fresh['revision'] = old['revision']
    for nid in changed:
        mark_downstream(fresh, nid)
    if len(fresh['nodes']) > MAX_NODES or len(fresh['edges']) > MAX_EDGES or len(fresh['groups']) > MAX_GROUPS or any(len(g['node_ids']) > MAX_GROUP_MEMBERS for g in fresh['groups']):
        raise HTTPException(422, f'完整制作画布超过容量（{MAX_NODES} 节点、{MAX_EDGES} 连线），请减少集数或拆分项目；未删减任何镜头')
    return fresh


def mark_downstream(graph, nid):
    pending, visited = [nid], set()
    nodes = {n['id']: n for n in graph['nodes']}
    outgoing = {}
    for e in graph['edges']: outgoing.setdefault(e['from'], []).append(e['to'])
    while pending:
        for target in outgoing.get(pending.pop(), []):
            if target in visited: continue
            visited.add(target); pending.append(target)
            node = nodes.get(target)
            if node and node.get('mujian') and node.get('assetId'):
                node['mujian']['mediaStale'] = True


def input_specs(graph, node):
    """Keep scripted slots first, then honor references added in the native canvas."""
    refs = deepcopy(node.get('mujian', {}).get('inputs', []))
    if node['type'] == 'timeline': return refs
    known = {r['nodeId'] for r in refs}
    kinds = {'asset': 'image', 'prop': 'image', 'character': 'image', 'audio': 'audio',
             'video-asset': 'video', 'video': 'video'}
    nodes = {n['id']: n for n in graph['nodes']}
    for edge in graph['edges']:
        source = nodes.get(edge['from'])
        if edge['to'] != node['id'] or edge.get('relation') == 'agent-flow' or not source or source['id'] in known: continue
        kind = kinds.get(source['type'])
        if not kind or (node['type'] != 'video' and kind != 'image'): continue
        refs.append({'nodeId': source['id'], 'kind': kind, 'role': source.get('title', '参考素材'), 'additional': True})
        known.add(source['id'])
    return refs


def resolve_inputs(host, graph, node):
    meta = node.get('mujian', {})
    if meta.get('needsReview') or meta.get('mediaStale') or meta.get('archived'):
        raise HTTPException(409, '剧本或前置素材有变化，请先核对本节点提示词和引用')
    resolved = []
    for ref in input_specs(graph, node):
        source = next((n for n in graph['nodes'] if n['id'] == ref['nodeId']), None)
        if not source or not any(e['from'] == source['id'] and e['to'] == node['id'] and e.get('relation') != 'agent-flow' for e in graph['edges']):
            raise HTTPException(422, '前置引用连线缺失，请重新连接')
        if any(source.get('mujian', {}).get(k) for k in ('needsReview', 'mediaStale', 'archived')):
            raise HTTPException(409, f'前置素材「{source["title"]}」待核对')
        aid = source.get('assetId') or source.get('referenceAssetId')
        if not aid and source['type'] == 'character' and source.get('characterId'):
            aid = host.get('character', source['characterId']).get('asset_id')
        if not aid:
            raise HTTPException(422, f'请先生成或绑定「{source["title"]}」')
        asset = host.require_live_asset(aid)
        if (asset['kind'] not in ('image', 'prop') if ref['kind'] == 'image' else asset['kind'] != ref['kind']):
            raise HTTPException(422, '前置素材类型不匹配')
        if not ref.get('additional') or asset['id'] not in resolved: resolved.append(asset['id'])
    if len(resolved) != len(set(resolved)):
        raise HTTPException(422, '多个参考位使用同一素材，请核对图片顺序')
    if node['type'] in ('asset', 'prop', 'character'):
        for aid in node.get('image_settings', {}).get('reference_asset_ids', []):
            asset = host.require_live_asset(aid)
            if asset['kind'] not in ('image', 'prop'): raise HTTPException(422, '生图引用请选择图片素材')
            if aid not in resolved: resolved.append(aid)
    elif node['type'] == 'video':
        # Includes native manual references, mentions and user ordering as well.
        native = host.video_graph_asset_ids(graph, node)
        if any(aid not in native for aid in resolved): raise HTTPException(422, '生成请求缺少前置素材')
        for aid in native: host.require_live_asset(aid)
        resolved = native
    return resolved


def binding_signature(graph, node):
    settings = node.get('settings') if node['type'] == 'video' else node.get('image_settings')
    settings = {k: v for k, v in (settings or {}).items() if k not in ('last_result_asset_id',)}
    sources = {r['nodeId'] for r in input_specs(graph, node)}
    return digest([node['id'], node.get('assetId'), settings, node.get('mujian', {}),
                   [(n['id'], n.get('assetId'), n.get('referenceAssetId')) for n in graph['nodes'] if n['id'] in sources],
                   [e for e in graph['edges'] if e['to'] == node['id']]])


def compile_reference_prompt(host, graph, node, prompt, asset_ids):
    """Resolve node mentions at submission time; drafts keep their stable tokens."""
    settings = node.get('settings') if node['type'] == 'video' else node.get('image_settings', {})
    labels, counts = {}, {}
    for aid in asset_ids:
        kind = host.require_live_asset(aid).get('kind', 'image')
        kind = 'image' if kind == 'prop' else kind
        counts[kind] = counts.get(kind, 0) + 1
        labels[aid] = {'image': '图片', 'video': '视频', 'audio': '音频'}.get(kind, '素材') + str(counts[kind])
    nodes = {n['id']: n for n in graph['nodes']}
    replacements = {}
    for mention in (settings or {}).get('mentions', []):
        token = mention.get('token')
        if not token or token not in prompt or mention.get('kind') in ('text', 'script'): continue
        source = nodes.get(mention.get('node_id'))
        aid = (source.get('referenceAssetId') or source.get('assetId') if source['type'] == 'video' else source.get('assetId')) if source else ('' if mention.get('node_id') else mention.get('asset_id'))
        if source and not aid and source.get('characterId'):
            aid = host.get('character', source['characterId']).get('asset_id')
        if aid not in labels: raise HTTPException(422, f'引用「{mention.get("name", "素材") }」尚未就绪，请先生成或绑定素材')
        replacements[token] = labels[aid]
    if not replacements: return prompt
    pattern = '|'.join(re.escape(token) for token in sorted(replacements, key=len, reverse=True))
    return re.sub(pattern, lambda match: replacements[match.group()], prompt)


def reconcile_bindings(host, pid):
    """Recover native node results even if the browser closed before binding."""
    graph = deepcopy(host.active_project(pid))
    if not any(n.get('mujian') for n in graph['nodes']): return graph
    tasks = sorted([t for kind in ('image_task', 'task') for t in host.listing(kind)
                    if t.get('project_id') == pid and t.get('studio_binding') and not t.get('deleted_at')],
                   key=lambda t: t.get('created_at', 0), reverse=True)
    considered, changed = set(), False
    for task in tasks:
        nid = task.get('node_id') or task.get('params', {}).get('node_id')
        if nid in considered: continue
        considered.add(nid)
        if task.get('stop_requested') or task.get('local_stop_requested'): continue
        if task['status'] not in ('done', 'succeeded'): continue
        node = next((n for n in graph['nodes'] if n['id'] == nid), None)
        if not node or binding_signature(graph, node) != task['studio_binding']: continue
        try:
            asset = host.video_history_asset(task['id']) if node['type'] == 'video' else task.get('asset')
            if not asset: continue
            asset = host.require_live_asset(asset['id'])
            if not str(asset.get('url', '')).startswith('/uploads/') or asset.get('file_status') == 'failed': continue
            if node.get('assetId') != asset['id']: mark_downstream(graph, nid)
            node.update(assetId=asset['id'], assetUrl=asset['url'])
            if node['type'] == 'video': node.update(referenceAssetId=asset['id'], referenceTaskId=task['id'])
            else: node['image_task_id'] = task['id']
            changed = True
        except HTTPException:
            continue
    if changed:
        try: return host.update_project(pid, host.Project(**graph))
        except HTTPException as error:
            if error.status_code != 409: raise
            return host.active_project(pid)
    return graph
