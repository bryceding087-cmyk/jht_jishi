"""Canvas-scoped commands. Models never choose an API, project, file, or code."""
from copy import deepcopy
import hashlib
import json
import math
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from studio_graph import resolve_inputs
from visual_styles import VisualStyleSelection


class CanvasOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Literal['add_node', 'update_node', 'update_nodes', 'move_nodes', 'delete_nodes', 'duplicate_nodes', 'connect', 'disconnect',
                  'create_group', 'update_group', 'ungroup', 'arrange', 'rename', 'clear',
                  'bind_asset', 'generate', 'cancel_generation', 'make_film', 'node_action', 'select', 'fit', 'zoom', 'pan', 'panel', 'toggle', 'view', 'undo', 'redo']
    args: dict = Field(default_factory=dict)


TOOL_ARGS = {
    'add_node': ['node'], 'update_node': ['node_id', 'changes'], 'update_nodes': ['node_ids', 'changes'], 'move_nodes': ['node_ids', 'dx', 'dy'], 'delete_nodes': ['node_ids'],
    'duplicate_nodes': ['node_ids', 'dx', 'dy'], 'connect': ['from', 'to', 'slot'], 'disconnect': ['from', 'to'],
    'create_group': ['group_id', 'node_ids', 'title', 'color'], 'update_group': ['group_id', 'changes'],
    'ungroup': ['group_id'], 'arrange': ['node_ids', 'layout'], 'rename': ['title'], 'clear': [],
    'bind_asset': ['node_id', 'asset_id'], 'generate': ['node_ids'], 'cancel_generation': ['node_ids'], 'make_film': [], 'node_action': ['node_id', 'value'], 'select': ['node_ids'],
    'fit': ['node_ids'], 'zoom': ['value'], 'pan': ['x', 'y'], 'panel': ['value'],
    'toggle': ['value', 'enabled'], 'view': ['value'], 'undo': [], 'redo': [],
}
PANELS = ['details', 'assets', 'history', 'minimap', 'shortcuts', 'help', 'add_node']
SETTINGS = {'prompt', 'negative_prompt', 'model_config_id', 'aspect_ratio', 'duration', 'resolution', 'size',
            'count', 'video_mode', 'mode', 'visual_style', 'reference_asset_ids', 'asset_ids', 'frame_asset_ids', 'speaker'}
NODE_FIELDS = {'title', 'text', 'x', 'y', 'settings', 'image_settings', 'audio_settings'}
NODE_TYPES = {'prompt', 'asset', 'prop', 'character', 'audio', 'video', 'video-asset', 'script', 'timeline'}
GRAPH_FIELDS = ('name', 'nodes', 'edges', 'groups', 'selected_asset_ids', 'viewport', 'folder_id')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 10000000:
        raise HTTPException(422, '画布坐标或数值无效')
    return value


def text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(422, '画布文本格式或长度无效')
    return value


def bound_project(host, run):
    pid = run.get('studio', {}).get('canvas_project_id')
    if not pid and run.get('creation_type') == 'canvas':
        pid = run.get('project_id')
    if not pid:
        raise HTTPException(403, '当前对话未获得画布操作权限')
    return host.active_project(pid)


def asset_ids(graph):
    found = set()
    for node in graph['nodes']:
        for key in ('assetId', 'referenceAssetId'):
            if node.get(key): found.add(node[key])
        for key in ('settings', 'image_settings', 'audio_settings'):
            settings = node.get(key) or {}
            if settings.get('last_result_asset_id'): found.add(settings['last_result_asset_id'])
            for field in ('asset_ids', 'reference_asset_ids', 'frame_asset_ids'):
                found.update(a for a in settings.get(field, []) if a)
    return found


def prepare(host, graph, operations, allowed_assets, seed):
    graph = deepcopy(graph)
    ui, generate = [], []
    changed = False
    def node(nid):
        result = next((n for n in graph['nodes'] if n['id'] == nid), None)
        if not result: raise HTTPException(403, '只能操作当前画布中存在的节点')
        return result
    def ids(value):
        if value == 'all': return [n['id'] for n in graph['nodes']]
        if not isinstance(value, list) or len(value) > 1600 or any(not isinstance(i, str) for i in value):
            raise HTTPException(422, '请指定当前画布的节点列表')
        for nid in value: node(nid)
        return list(dict.fromkeys(value))
    def check_refs(values):
        if not isinstance(values, list) or any(not isinstance(a, str) or a not in allowed_assets for a in values):
            raise HTTPException(403, '只可使用当前画布或本条消息明确引用的素材')
        for aid in values: host.require_live_asset(aid)
    def changes(target, values):
        if not isinstance(values, dict) or set(values) - NODE_FIELDS:
            raise HTTPException(422, '不允许修改节点的内部绑定或权限字段')
        for key, value in values.items():
            if key in ('x', 'y'): target[key] = number(value)
            elif key in ('title', 'text'):
                value = text(value, 160 if key == 'title' else 60000)
                if key == 'text' and target['type'] == 'prompt':
                    target.setdefault('settings', {})['prompt'] = value
                    target.pop('text', None)
                else: target[key] = value
            else:
                if not isinstance(value, dict) or set(value) - SETTINGS: raise HTTPException(422, '节点参数包含未允许字段')
                for field in ('asset_ids', 'reference_asset_ids', 'frame_asset_ids'):
                    if field in value: check_refs(value[field])
                if 'prompt' in value: text(value['prompt'], 4000)
                for field in ('duration', 'count'):
                    if field in value:
                        number(value[field])
                        if value[field] <= 0: raise HTTPException(422, '时长和数量必须大于零')
                for field in ('model_config_id', 'aspect_ratio', 'resolution', 'size', 'video_mode', 'mode', 'speaker', 'negative_prompt'):
                    if field in value: text(value[field], 1000)
                if value.get('visual_style') is not None:
                    value['visual_style'] = VisualStyleSelection.model_validate(value['visual_style']).model_dump()
                target[key] = {**(target.get(key) or {}), **deepcopy(value)}
    for index, op in enumerate(operations):
        name, args = op['name'], op['args']
        if name not in TOOL_ARGS or set(args) - set(TOOL_ARGS[name]):
            raise HTTPException(403, '该工具不接受画布 ID、接口或其他越权参数')
        if name == 'add_node':
            data = deepcopy(args.get('node'))
            if not isinstance(data, dict) or set(data) - (NODE_FIELDS | {'id', 'type'}): raise HTTPException(422, '新增节点格式无效')
            if data.get('type') not in NODE_TYPES: raise HTTPException(422, '未知节点类型')
            nid = data.get('id') or 'canvas-' + str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ':' + str(index)))
            if not isinstance(nid, str) or len(nid) > 160 or any(n['id'] == nid for n in graph['nodes']): raise HTTPException(422, '新节点编号无效或重复')
            result = {'id': nid, 'type': data['type'], 'title': '新节点', 'x': 100, 'y': 100}
            changes(result, {k: v for k, v in data.items() if k in NODE_FIELDS})
            graph['nodes'].append(result)
        elif name == 'update_node': changes(node(args.get('node_id')), args.get('changes'))
        elif name == 'update_nodes':
            for nid in ids(args.get('node_ids')): changes(node(nid), args.get('changes'))
        elif name == 'move_nodes':
            chosen = set(ids(args.get('node_ids')))
            dx, dy = number(args.get('dx', 0)), number(args.get('dy', 0))
            for nid in chosen:
                item = node(nid); item['x'] = number(item['x'] + dx); item['y'] = number(item['y'] + dy)
            for group in graph['groups']:
                if set(group['node_ids']).issubset(chosen):
                    if 'x' in group: group['x'] = number(group['x'] + dx)
                    if 'y' in group: group['y'] = number(group['y'] + dy)
        elif name in ('delete_nodes', 'clear'):
            removed = set(ids(args.get('node_ids')) if name == 'delete_nodes' else ids('all'))
            graph['nodes'] = [n for n in graph['nodes'] if n['id'] not in removed]
            graph['edges'] = [e for e in graph['edges'] if e['from'] not in removed and e['to'] not in removed]
            for group in graph['groups']: group['node_ids'] = [i for i in group['node_ids'] if i not in removed]
            graph['groups'] = [g for g in graph['groups'] if g['node_ids']]
        elif name == 'duplicate_nodes':
            copied = {}
            for nid in ids(args.get('node_ids')):
                old = node(nid)
                new_id = 'canvas-' + str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ':' + str(index) + ':' + nid))
                copied[nid] = new_id
                data = deepcopy(old)
                data.update(id=new_id, x=old['x'] + number(args.get('dx', 48)), y=old['y'] + number(args.get('dy', 48)))
                for key in ('mujian', 'agent_run_id', 'agent_key', 'agent_type'): data.pop(key, None)
                for key in ('image_settings', 'audio_settings'):
                    if key in data:
                        for task_key in ('image_task_id', 'audio_task_id'): data[key].pop(task_key, None)
                graph['nodes'].append(data)
            graph['edges'].extend({**deepcopy(e), 'from': copied[e['from']], 'to': copied[e['to']]} for e in list(graph['edges']) if e['from'] in copied and e['to'] in copied)
        elif name in ('connect', 'disconnect'):
            source, target = node(args.get('from')), node(args.get('to'))
            if source['id'] == target['id']: raise HTTPException(422, '节点不能连接自身')
            pair = (source['id'], target['id'])
            graph['edges'] = [e for e in graph['edges'] if (e['from'], e['to']) != pair]
            if name == 'connect': graph['edges'].append({'from': pair[0], 'to': pair[1], **({'slot': text(args['slot'], 40)} if 'slot' in args else {})})
        elif name in ('create_group', 'update_group', 'ungroup'):
            gid = args.get('group_id')
            group = next((g for g in graph['groups'] if g['id'] == gid), None)
            if name == 'create_group':
                if group: raise HTTPException(422, '组编号重复')
                group = {'id': gid or 'group-' + str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ':' + str(index))), 'title': args.get('title', '新分组'), 'color': args.get('color', '#999999'), 'node_ids': ids(args.get('node_ids'))}
                if not isinstance(group['id'], str) or len(group['id']) > 160: raise HTTPException(422, '组编号无效')
                for other in graph['groups']: other['node_ids'] = [i for i in other['node_ids'] if i not in group['node_ids']]
                graph['groups'] = [g for g in graph['groups'] if g['node_ids']] + [group]
            elif not group: raise HTTPException(403, '只能操作当前画布的分组')
            elif name == 'ungroup': graph['groups'].remove(group)
            else:
                patch = args.get('changes')
                if not isinstance(patch, dict) or set(patch) - {'title', 'color', 'node_ids', 'x', 'y', 'width', 'height'}: raise HTTPException(422, '组属性无效')
                if 'node_ids' in patch:
                    patch['node_ids'] = ids(patch['node_ids'])
                    for other in graph['groups']:
                        if other is not group: other['node_ids'] = [i for i in other['node_ids'] if i not in patch['node_ids']]
                for key in ('x', 'y', 'width', 'height'):
                    if key in patch: number(patch[key])
                group.update(patch)
        elif name == 'arrange':
            chosen = ids(args.get('node_ids', 'all'))
            layout = args.get('layout', 'grid')
            if layout not in ('grid', 'horizontal', 'vertical'): raise HTTPException(422, '未知排列方式')
            columns = max(1, math.ceil(math.sqrt(len(chosen)))) if layout == 'grid' else len(chosen) if layout == 'horizontal' else 1
            for i, nid in enumerate(chosen): node(nid).update(x=100 + i % max(1, columns) * 400, y=100 + i // max(1, columns) * 420)
            for group in graph['groups']:
                for key in ('x', 'y', 'width', 'height'): group.pop(key, None)
        elif name == 'rename': graph['name'] = text(args.get('title'), 80)
        elif name == 'bind_asset':
            target = node(args.get('node_id')); aid = args.get('asset_id'); check_refs([aid])
            asset = host.require_live_asset(aid)
            allowed = {'asset': ('image', 'prop'), 'prop': ('image', 'prop'), 'character': ('image', 'prop'), 'video-asset': ('video',), 'audio': ('audio',)}
            if asset['kind'] not in allowed.get(target['type'], ()): raise HTTPException(422, '素材类型与节点不匹配')
            target['assetId'] = aid
        elif name == 'generate':
            chosen = ids(args.get('node_ids'))
            if not chosen or len(chosen) > 20: raise HTTPException(422, '单次生成请选择 1 至 20 个节点')
            for nid in chosen:
                if node(nid)['type'] not in ('asset', 'prop', 'character', 'video', 'audio'): raise HTTPException(422, '所选节点不支持媒体生成')
            generate.extend(chosen)
        elif name == 'node_action':
            target = node(args.get('node_id'))
            if args.get('value') not in ('play', 'capture', 'download', 'polish', 'script', 'expand', 'reference'): raise HTTPException(422, '未知节点功能')
            ui.append({'name': name, 'node_id': target['id'], 'value': args['value']})
        elif name in ('select', 'fit'):
            selected = ids(args.get('node_ids', 'all')); ui.append({'name': name, 'node_ids': selected})
        elif name == 'zoom':
            value = number(args.get('value'))
            if not .05 <= value <= 8: raise HTTPException(422, '缩放比例须为 0.05 至 8')
            ui.append({'name': name, 'value': value})
        elif name == 'pan': ui.append({'name': name, 'x': number(args.get('x')), 'y': number(args.get('y'))})
        elif name == 'panel':
            if args.get('value') not in PANELS: raise HTTPException(422, '未知画布面板')
            ui.append({'name': name, 'value': args['value']})
        elif name == 'toggle':
            if args.get('value') not in ('snap', 'edges', 'minimap') or type(args.get('enabled')) is not bool: raise HTTPException(422, '开关参数无效')
            ui.append({'name': name, **args})
        elif name == 'view':
            if args.get('value') not in ('workflow', 'storyboard'): raise HTTPException(422, '未知画布视图')
            ui.append({'name': name, 'value': args['value']})
        else: raise HTTPException(422, '撤销和重做必须单独提交')
        if name not in ('generate', 'node_action', 'select', 'fit', 'zoom', 'pan', 'panel', 'toggle', 'view'): changed = True
    # All commands are validated against one copy before any mutation or paid work.
    host.validate_graph(host.Project(**{k: graph[k] for k in GRAPH_FIELDS if k in graph}), host.active_project(graph['id']))
    return graph, changed, ui, list(dict.fromkeys(generate))


def execute_graph(host, run, operations, expected_revision, request_id, attached_assets=()):
    graph = bound_project(host, run)
    pid = graph['id']
    receipt_id = 'canvas-command:' + hashlib.sha256((run['id'] + ':' + request_id).encode()).hexdigest()
    previous = host.raw_record('canvas_command', receipt_id)
    if previous:
        if previous.get('project_id') != pid or previous.get('run_id') != run['id']: raise HTTPException(403, '操作回执不属于当前画布')
        return previous
    if graph['revision'] != expected_revision: raise HTTPException(409, '画布已更新，本轮操作没有执行；请基于最新内容重试')
    restore = None
    if len(operations) == 1 and operations[0]['name'] in ('undo', 'redo'):
        if operations[0]['args']: raise HTTPException(403, '撤销操作不能指定其他画布')
        undo = operations[0]['name'] == 'undo'
        candidates = [r for r in host.listing('canvas_command') if r.get('project_id') == pid and r.get('changed') and not r.get('restoration') and r.get('state') == ('applied' if undo else 'undone')]
        restore = max(candidates, key=lambda r: r['created_at'] if undo else r.get('undo_time', 0), default=None)
        if not restore: raise HTTPException(409, '没有可撤销或重做的 Agent 画布修改')
        expected = restore['after' if undo else 'before']
        comparable = lambda g: {k: g.get(k) for k in GRAPH_FIELDS if k != 'viewport'}
        if graph['revision'] != restore.get('after_revision' if undo else 'undo_revision') and comparable(graph) != comparable(expected):
            raise HTTPException(409, '画布已有后续修改，未覆盖当前内容')
        updated = {**graph, **deepcopy(restore['before' if undo else 'after'])}
        updated['viewport'] = deepcopy(graph.get('viewport', {}))
        changed, ui, generate = True, [], []
    else:
        updated, changed, ui, generate = prepare(host, graph, operations, asset_ids(graph) | set(attached_assets), receipt_id)
    for nid in generate:
        submit_node(host, run, pid, nid, 'canvas-preflight', graph_override=updated, validate_only=True)
    payload = host.Project(**{k: updated[k] for k in GRAPH_FIELDS if k in updated})
    host.validate_graph(payload, graph)
    result = {'id': receipt_id, 'owner_id': run['owner_id'], 'run_id': run['id'], 'project_id': pid,
              'created_at': time.time(), 'changed': changed, 'state': 'applied', 'restoration': bool(restore),
              'before': {k: deepcopy(graph[k]) for k in GRAPH_FIELDS if k in graph},
              'after': payload.model_dump(exclude={'revision'}), 'after_revision': expected_revision + int(changed),
              'ui': ui, 'generate': generate}
    # Compare revision and save the graph plus receipt in the same transaction.
    with host.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old_receipt = db.execute('SELECT body FROM records WHERE kind=? AND id=?', ('canvas_command', receipt_id)).fetchone()
        if old_receipt: return json.loads(old_receipt[0])
        row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', ('project', pid)).fetchone()
        latest = host.require_access('project', json.loads(row[0])) if row else None
        if not latest or latest.get('deleted_at') or latest['revision'] != expected_revision: raise HTTPException(409, '画布已更新，本轮操作没有执行')
        if changed:
            body = {**latest, **payload.model_dump(exclude={'revision'}), 'revision': result['after_revision'], 'updated_at': time.time()}
            db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(body, ensure_ascii=False), 'project', pid))
        if restore:
            restore.update(state='undone' if operations[0]['name'] == 'undo' else 'applied')
            if operations[0]['name'] == 'undo': restore['undo_time'] = time.time()
            restore['undo_revision' if operations[0]['name'] == 'undo' else 'after_revision'] = result['after_revision']
            db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(restore, ensure_ascii=False), 'canvas_command', restore['id']))
        db.execute('INSERT INTO records(kind,id,body) VALUES(?,?,?)', ('canvas_command', receipt_id, json.dumps(result, ensure_ascii=False)))
    return result


def submit_node(host, run, pid, nid, request_id, *, graph_override=None, validate_only=False):
    graph = bound_project(host, run)
    if graph['id'] != pid: raise HTTPException(403, '画布权限已变化')
    if graph_override is not None:
        if graph_override.get('id') != pid: raise HTTPException(403, '不能使用其他画布的图形')
        graph = graph_override
    node = next((n for n in graph['nodes'] if n['id'] == nid), None)
    if not node: raise HTTPException(403, '节点不在当前画布中')
    refs = resolve_inputs(host, graph, node)
    key = 'settings' if node['type'] == 'video' else 'audio_settings' if node['type'] == 'audio' else 'image_settings'
    settings = node.get(key) or {}
    prompt = settings.get('prompt') or node.get('text')
    if node['type'] == 'video':
        incoming = [n for e in graph['edges'] if e['to'] == nid and e.get('relation') != 'agent-flow' for n in graph['nodes'] if n['id'] == e['from'] and n['type'] in ('prompt', 'script')]
        source = next((n for n in incoming if n['id'] == settings.get('prompt_source_id')), next(iter(incoming), None))
        if source and source['type'] == 'prompt': prompt = source.get('settings', {}).get('prompt') or source.get('text') or prompt
        if source and source['type'] == 'script':
            inherited = host.script_video_settings(source.get('script_data') or {}, settings.get('script_scene_index'))
            prompt = inherited['prompt']; settings = {**settings, 'duration': inherited['duration']}
    if not prompt: raise HTTPException(422, '请先填写节点提示词')
    title = node.get('title') or '画布生成'
    model_id = settings.get('model_config_id') or None
    if node['type'] == 'video':
        model = host.internal_registry_model('video', model_id)
        mode = settings.get('video_mode') or settings.get('mode') or ('image-to-video' if refs else 'text-to-video')
        request = host.Task(project_id=pid, node_id=nid, request_id=request_id, prompt=prompt,
            model_config_id=model['id'], model=model['model_id'], mode=mode, asset_ids=refs,
            aspect_ratio=settings.get('aspect_ratio', '16:9'), duration=settings.get('duration', 4),
            resolution=settings.get('resolution', '768p'), negative_prompt=settings.get('negative_prompt', ''), visual_style=settings.get('visual_style'))
        if validate_only: return {'kind': 'task'}
        task = host.submit_video_task(request)
        return {'kind': 'task', 'id': task['id'], 'node_id': nid}
    if node['type'] == 'audio':
        request = host.AudioGenerationRequest(project_id=pid, request_id=request_id,
            name=title, prompt=prompt, model_config_id=model_id or '', mode=settings.get('mode', 'sfx'),
            reference_asset_ids=refs, speaker=settings.get('speaker', ''))
        if validate_only: return {'kind': 'audio_task'}
        task = host.create_audio_generation(request)
        return {'kind': 'audio_task', 'id': task['id'], 'node_id': nid, 'settings_key': key, 'task_key': 'audio_task_id'}
    request = host.ImageGenerationRequest(project_id=pid, node_id=nid, request_id=request_id,
        name=title, prompt=prompt, model_config_id=model_id or '', aspect_ratio=settings.get('aspect_ratio', 'adaptive'),
        size=settings.get('size', '2K'), count=settings.get('count', 1), reference_asset_ids=refs,
        asset_kind='prop' if node['type'] == 'prop' else 'image', visual_style=settings.get('visual_style'))
    if validate_only: return {'kind': 'image_task'}
    task = host.create_image_generation(request)
    return {'kind': 'image_task', 'id': task['id'], 'node_id': nid, 'settings_key': key, 'task_key': 'image_task_id'}


def cancel_nodes(host, run, node_ids):
    graph = bound_project(host, run)
    nodes = {n['id']: n for n in graph['nodes']}
    if not isinstance(node_ids, list) or not node_ids or len(node_ids) > 100 or any(nid not in nodes for nid in node_ids):
        raise HTTPException(403, '只能取消当前画布节点的排队任务')
    chosen = {}
    for kind in ('image_task', 'task'):
        for task in host.listing(kind):
            if task.get('project_id') != graph['id'] or task.get('deleted_at'): continue
            nid = task.get('node_id') or task.get('params', {}).get('node_id')
            if kind == 'image_task':
                nid = nid or next((i for i in node_ids if nodes[i].get('image_settings', {}).get('image_task_id') == task['id']), None)
            if nid in node_ids and task.get('created_at', 0) >= chosen.get(nid, ('', {}))[1].get('created_at', -1): chosen[nid] = (kind, task)
    if len(chosen) != len(set(node_ids)): raise HTTPException(409, '部分节点没有可取消的图片或视频排队任务')
    with host.generation_queue.lock:
        for kind, task in chosen.values():
            job = host.generation_queue.job(kind, task['id'])
            if not job or job['state'] not in ('waiting', 'credentials', 'cancelled'): raise HTTPException(409, '任务已提交给模型，不能取消供应商中的生成；未取消其他排队任务')
        return [{'kind': kind, 'id': host.generation_queue.cancel(kind, task['id'])['id']} for kind, task in chosen.values()]
