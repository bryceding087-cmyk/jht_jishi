"""Mujian's optional JHT render adapter. Install inside JHT's authenticated scope.

No endpoint is installed on import. Call only after a user requests rendering.
Assets are looked up again on the server; never trust IDs/durations from a model.
"""
import math
import tempfile
import uuid
from pathlib import Path

from fastapi import HTTPException
from agent_media import asset_path, inspect_file, execute, render_timeline


def _inputs(host, graph, node):
    if node.get('mujian', {}).get('needsReview') or node.get('mujian', {}).get('mediaStale'):
        raise HTTPException(409, '剪辑节点变化后需要重新核对')
    result = {}
    for ref in node.get('mujian', {}).get('inputs', []):
        source = next((n for n in graph['nodes'] if n['id'] == ref['nodeId']), None)
        if not source or not any(e['from'] == source['id'] and e['to'] == node['id'] for e in graph['edges']):
            raise HTTPException(422, '剪辑前置节点或连线缺失')
        if source.get('mujian', {}).get('mediaStale') or source.get('mujian', {}).get('needsReview'):
            raise HTTPException(409, '前置视频素材待重新核对')
        asset = host.require_live_asset(source.get('assetId', ''))
        if asset.get('kind') != 'video':
            raise HTTPException(422, '剪辑输入必须为已完成的视频素材')
        file = asset_path(host, asset['id'])
        result[source['id']] = (asset, file, inspect_file(file))
    return result


def render_node(host, project_id, node_id, expected_revision, progress=lambda _: None, cancelled=lambda: False):
    """Return a saved video asset. Binding it to a graph is a separate versioned write.

    The caller must retain the user's access_scope for background workers and
    recheck graph revision before binding. A conflict retains the result in media
    history; it must not overwrite a concurrently edited canvas.
    """
    graph = host.active_project(project_id)
    if graph.get('revision') != expected_revision:
        raise HTTPException(409, '画布已更新，请刷新后再合成')
    node = next((n for n in graph['nodes'] if n['id'] == node_id), None)
    if not node or node.get('type') != 'timeline':
        raise HTTPException(422, '剪辑节点不存在')
    inputs = _inputs(host, graph, node)
    clips = node.get('clips', [])
    if not clips or len({c.get('nodeId') for c in clips}) != len(clips) or set(inputs) != {c.get('nodeId') for c in clips}:
        raise HTTPException(422, '剪辑顺序缺少输入或重复引用')
    if node.get('mujian', {}).get('role') != 'final':
        values = []
        for clip in clips:
            duration = clip.get('duration')
            if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0:
                raise HTTPException(422, '剪辑时长无效')
            values.append({'asset_id': inputs[clip['nodeId']][0]['id'], 'duration': duration,
                           'mute_original': clip.get('muteOriginal', False)})
        return render_timeline(host, {'project_id': project_id, 'title': node['title'],
            'aspect_ratio': node.get('aspect_ratio', '16:9'), 'clips': values,
            'subtitles': [], 'audio': [], 'brand': ''}, progress, cancelled)
    if len(clips) > 60:
        raise HTTPException(422, '整剧合成最多支持 60 集')
    # Use actual complete episodes, not their writing-time budgets. Normalize
    # codec/audio before concatenating; paths in the concat file are ours alone.
    ratio = node.get('aspect_ratio', '16:9')
    sizes = {'16:9': (1280, 720), '9:16': (720, 1280), '1:1': (960, 960)}
    if ratio not in sizes:
        raise HTTPException(422, '当前成片合成不支持此画幅')
    width, height = sizes[ratio]
    total = sum(inputs[c['nodeId']][2]['duration'] for c in clips)
    if not math.isfinite(total) or not 0 < total <= 7200:
        raise HTTPException(422, '整剧实际时长须在 2 小时以内')
    output = host.UPLOADS / (str(uuid.uuid4()) + '.mp4')
    with tempfile.TemporaryDirectory(prefix='mujian-assembly-', dir=host.DATA) as directory:
        work = Path(directory)
        try:
            for i, clip in enumerate(clips):
                if cancelled():
                    raise HTTPException(409, '已停止成片合成')
                _, file, meta = inputs[clip['nodeId']]
                progress(f'正在合成第 {i+1}/{len(clips)} 集')
                execute(['-i', str(file), '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                    '-map', '0:v:0', '-map', '0:a:0' if meta['has_audio'] else '1:a:0',
                    '-t', str(meta['duration']), '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-ar', '48000', '-ac', '2', str(work/f'episode{i}.mp4')], timeout=900)
            (work/'concat.txt').write_text(''.join(f"file 'episode{i}.mp4'\n" for i in range(len(clips))), encoding='ascii')
            if cancelled():
                raise HTTPException(409, '已停止成片合成')
            execute(['-f', 'concat', '-safe', '1', '-i', 'concat.txt', '-c', 'copy', '-movflags', '+faststart', str(output)], cwd=work, timeout=900)
            meta = inspect_file(output)
            if abs(meta['duration']-total) > max(.5, len(clips)*.1):
                raise HTTPException(422, '成片实际时长与分集素材不一致')
            return host.asset_public(host.save('asset', {'id': str(uuid.uuid4()), 'name': node['title']+'.mp4',
                'kind': 'video', 'url': '/uploads/'+output.name, 'mime_type': 'video/mp4',
                'size_bytes': output.stat().st_size, 'project_id': project_id, 'source': 'generated',
                'tags': ['幕间成片'], 'folder_id': '', 'created_at': host.time.time(),
                'duration': meta['duration'], 'agent_render': True}))
        except Exception:
            output.unlink(missing_ok=True)
            raise
