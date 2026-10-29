"""Bounded, read-only canvas facts for Studio; never serialize provider configs."""
from collections import Counter
import json

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class CanvasSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selected_node_ids: list[str] = Field(default_factory=list, max_length=100)
    selected_group_id: str | None = Field(default=None, max_length=160)
    snap_to_grid: bool | None = None
    show_edges: bool | None = None
    minimap_visible: bool | None = None
    view_mode: Literal['workflow', 'storyboard'] | None = None


STUDIO_SKILLS = [
    {'id': 'canvas-context', 'title': '画布理解', 'description': '读取当前画布名称、节点内容、连线、分组、素材元数据与生成状态；解释选中节点和已有内容。'},
    {'id': 'canvas-control', 'title': '画布操作', 'description': '仅操作绑定画布的节点、参数、连线、分组、排版、视图、生成任务，以及 Agent 修改的撤销和重做；不能访问其他画布或修改全局设置。'},
    {'id': 'studio-writing', 'title': '剧本创作', 'description': '创作和修订故事设定、分集大纲与完整正文，保留版本和上下文连续性。'},
    {'id': 'studio-assets', 'title': '资产与分镜拆解', 'description': '从完整剧本提取人物、场景、道具、镜头和提示词，保留原文出处与参考关系。'},
    {'id': 'studio-prompt-optimization', 'title': '节点提示词优化', 'description': '创建制作画布时逐项运用 Seedance 提示词专家及影视创作技能，优化角色、场景、道具、首尾帧和视频提示词，保持剧本与素材引用一致。'},
    {'id': 'studio-media', 'title': '图片与视频生成', 'description': '用户明确要求后使用已配置模型生成图片或单个视频；需要实际可用的模型和参考素材。'},
    {'id': 'studio-production', 'title': '制作画布与成片', 'description': '依据剧本创建分组画布；通过制作成片操作执行素材生成、分集剪辑和整剧合成。'},
]


def bounded(items, limit, budget):
    result, used = [], 0
    for item in items:
        cost = len(json.dumps(item, ensure_ascii=False))
        if len(result) >= limit or used + cost > budget:
            break
        result.append(item)
        used += cost
    return result


def canvas_context(host, run, selection=None):
    pid = run.get('studio', {}).get('canvas_project_id')
    if not pid and run.get('creation_type') == 'canvas':
        pid = run.get('project_id')
    if not pid:
        return {'available': False, 'reason': '本对话尚未关联画布'}
    try:
        graph = host.active_project(pid)
    except HTTPException as error:
        if error.status_code not in (403, 404, 410):
            raise
        return {'available': False, 'reason': '关联画布不存在或无权访问'}
    # Worker scopes authorize the graph read. All asset/task metadata must stay
    # within that same scope, even when historical graph references are stale.
    nodes = graph.get('nodes', [])
    ids = {n['id'] for n in nodes}
    selection = selection or {}
    selected = [nid for nid in selection.get('selected_node_ids', []) if nid in ids]
    groups = graph.get('groups', [])
    group_id = selection.get('selected_group_id')
    group = next((g for g in groups if g['id'] == group_id), None)
    if group:
        selected = list(dict.fromkeys([*selected, *(nid for nid in group.get('node_ids', []) if nid in ids)]))[:100]
    selected_set = set(selected)
    tasks, by_task_id = {}, {}
    for kind in ('task', 'image_task', 'audio_task'):
        for task in host.listing(kind):
            if task.get('project_id') != pid or task.get('deleted_at'):
                continue
            by_task_id[task['id']] = task
            nid = task.get('node_id') or task.get('params', {}).get('node_id')
            if nid in ids and task.get('created_at', 0) >= tasks.get(nid, {}).get('created_at', -1):
                tasks[nid] = task
    for node in nodes:
        for settings_name, task_key in (('image_settings', 'image_task_id'), ('audio_settings', 'audio_task_id')):
            task = by_task_id.get((node.get(settings_name) or {}).get(task_key))
            if task and task.get('created_at', 0) >= tasks.get(node['id'], {}).get('created_at', -1):
                tasks[node['id']] = task
    assets = {}

    def asset_info(aid):
        if not isinstance(aid, str) or not aid:
            return None
        if aid not in assets:
            try:
                asset = host.require_live_asset(aid)
                assets[aid] = {key: asset[key] for key in ('id', 'name', 'kind', 'width', 'height', 'duration') if key in asset}
                if 'name' in assets[aid]:
                    assets[aid]['name'] = str(assets[aid]['name'])[:256]
            except HTTPException as error:
                if error.status_code not in (403, 404, 410):
                    raise
                assets[aid] = {'id': aid, 'available': False}
        return assets[aid]

    def describe(node):
        item = {key: node[key] for key in ('id', 'type', 'title', 'x', 'y') if key in node}
        text_limit = 12000 if node['id'] in selected_set else 1200
        if node.get('text'):
            text = str(node['text'])
            item.update(text=text[:text_limit], text_length=len(text), text_truncated=len(text) > text_limit)
        for name in ('settings', 'image_settings', 'audio_settings'):
            settings = node.get(name) or {}
            safe = {key: settings[key] for key in ('model_config_id', 'model', 'aspect_ratio', 'duration', 'resolution', 'mode', 'count') if key in settings}
            if settings.get('prompt'):
                prompt = str(settings['prompt'])
                safe.update(prompt=prompt[:text_limit], prompt_truncated=len(prompt) > text_limit)
            if safe:
                item[name] = safe
        script = node.get('script_data') or {}
        if script:
            item['script'] = {'title': str(script.get('title', ''))[:160], 'shot_count': len(script.get('shots') or [])}
        refs = [node.get('assetId'), node.get('referenceAssetId')]
        for name in ('settings', 'image_settings', 'audio_settings'):
            settings = node.get(name) or {}
            refs.extend(settings.get('asset_ids') or [])
            refs.extend(settings.get('reference_asset_ids') or [])
            refs.append(settings.get('last_result_asset_id'))
        item['assets'] = [info for aid in dict.fromkeys(a for a in refs if isinstance(a, str)) if (info := asset_info(aid))][:12]
        if node['id'] in tasks:
            task = tasks[node['id']]
            item['generation'] = {key: task[key] for key in ('id', 'status', 'queue_state') if key in task}
        return item

    details = bounded((describe(n) for n in sorted(nodes, key=lambda n: n['id'] not in selected_set)), 100, 22000)
    edge_details = bounded(({k: e[k] for k in ('from', 'to', 'slot', 'role') if k in e} for e in graph.get('edges', [])), 200, 6000)
    group_details = bounded(({'id': g['id'], 'title': str(g.get('title', ''))[:160], 'node_ids': g.get('node_ids', [])[:100], 'node_count': len(g.get('node_ids', []))} for g in groups), 60, 4000)
    return {
        'available': True,
        'project': {key: graph[key] for key in ('id', 'name', 'revision') if key in graph},
        'summary': {'node_count': len(nodes), 'edge_count': len(graph.get('edges', [])), 'group_count': len(groups),
                    'node_types': dict(Counter(n.get('type', 'unknown') for n in nodes)),
                    'generation_states': dict(Counter(t.get('status', 'unknown') for t in tasks.values()))},
        'selection': {'selected_node_ids': selected, 'selected_group_id': group['id'] if group else None},
        'viewport': {k: graph.get('viewport', {})[k] for k in ('x', 'y', 'zoom') if k in graph.get('viewport', {})},
        'view_controls': {k: selection[k] for k in ('snap_to_grid', 'show_edges', 'minimap_visible', 'view_mode') if selection.get(k) is not None},
        'nodes': details, 'edges': edge_details, 'groups': group_details,
        'omitted_details': {'nodes': len(nodes) - len(details), 'edges': len(graph.get('edges', [])) - len(edge_details), 'groups': len(groups) - len(group_details)},
        'media_access': '仅提供素材元数据与已有文本，未分析图片像素或视频画面。',
    }
