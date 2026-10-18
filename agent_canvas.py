"""Keep Agent output on the same editable canvas as manually created work.

The migration removes internal documents and duplicate result cards, without
changing media, generation parameters, or positions the user has adjusted.
"""
from copy import deepcopy
import json
import math
import re
import uuid

LAYOUT_VERSION = 2
NODE_WIDTH, NODE_HEIGHT = 440, 340
STEP_X, STEP_Y = 580, 400


def node_id(rid, key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'{rid}:node:{key}'))


def known_keys(run):
    keys = {'source', 'working', 'script', 'bgm', 'final'}
    keys.update('doc:'+name for name in ('director', 'analysis', 'creative', 'creative_review', 'draft', 'storyboard', 'music_brief', 'timeline', 'quality', 'brand'))
    keys.update('doc:'+name for name in run.get('artifacts', {}))
    # Include historical slots, even when the latest revision no longer uses
    # them: their existing media still belongs to the user.
    keys.update(f'{prefix}_{i}' for prefix, count in [('visual', 40), ('anchor', 40), ('shot', 40)] for i in range(1, count+1))
    scenes = run.get('artifacts', {}).get('storyboard', {}).get('payload', {}).get('scenes', [])
    keys.update(scene['id'] for scene in scenes if scene.get('id'))
    keys.update(run.get('videos', {}))
    keys.update('character:'+key for key in list(keys) if key.startswith('visual_'))
    keys.update('result:'+key for key in list(keys) if key.startswith('shot_'))
    keys.update('speech:line_'+str(i) for i in range(1, 201))
    keys.update(run.get('bindings', {}))
    keys.update('speech:'+key for key in run.get('speech', {}))
    return {node_id(run['id'], key): key for key in keys}


def column_for(key):
    if key in ('source', 'working'):
        return 0
    if key.startswith(('visual_', 'anchor_', 'character:', 'speech:')) or key == 'bgm':
        return 1
    if key == 'script':
        return 2
    if key.startswith(('shot_', 'result:')):
        return 3
    return 4


def order_for(key):
    number = re.search(r'(\d+)$', key)
    number = int(number[1]) if number else 0
    if key.startswith(('anchor_', 'speech:')):
        return (2, number, key)
    if key == 'bgm':
        return (3, 0, key)
    return (1, number, key)


def _prompt(node):
    return node.get('settings', {}).get('prompt', '')


def _internal_document(node, key, run, artifact_contents):
    name = key.split(':', 1)[1]
    artifact = run.get('artifacts', {}).get(name, {})
    originals = []
    if isinstance(artifact.get('content'), str):
        originals.append(artifact['content'])
    for lookup in (key, name):
        values = artifact_contents.get(lookup, [])
        originals.extend([values] if isinstance(values, str) else values)
    text = _prompt(node)
    if originals:
        return text in originals
    # Old versions sometimes left a document whose artifact index was reset.
    # With no original to compare, only discard structured machine output.
    try:
        return isinstance(json.loads(text), (dict, list))
    except (ValueError, TypeError):
        return False


def sync_agent_canvas(graph, run, *, force=False, artifact_contents=None):
    """Simplify this run's nodes and lay out untouched/new output in place.

    ``artifact_contents`` may include historical document bodies by artifact
    name or ``doc:name``. Manually rewritten documents become ordinary nodes.
    Returns whether the graph changed; this function never writes media/data.
    """
    before = deepcopy((graph['nodes'], graph['edges']))
    rid = run['id']
    owned = [n for n in graph['nodes'] if n.get('agent_run_id') == rid]
    owned_ids = {n['id'] for n in owned}
    keys = known_keys(run)
    canonical, baselines = {}, {}
    for index, node in enumerate(owned):
        key = node.get('agent_key') or keys.get(node['id'])
        node.pop('agent_stage', None)
        baselines[node['id']] = node.get('agent_layout') or {'x': 100+index % 4*340, 'y': 100+index//4*280}
        # A copied user node can retain metadata. Only canonical IDs identify
        # nodes which the Agent can merge, remove or automatically position.
        if key and node['id'] == node_id(rid, key):
            node['agent_key'] = key
            canonical[key] = node

    removed, aliases = set(), {}
    for key, node in list(canonical.items()):
        if not key.startswith('doc:'):
            continue
        if _internal_document(node, key, run, artifact_contents or {}):
            removed.add(node['id'])
        else:
            for field in ('agent_run_id', 'agent_key', 'agent_layout', 'agent_layout_pending'):
                node.pop(field, None)
        canonical.pop(key)

    for key, result in list(canonical.items()):
        if not key.startswith('result:'):
            continue
        shot = canonical.get(key.split(':', 1)[1])
        if not shot or shot.get('type') != 'video':
            continue
        # A user may have replaced one of the assets. Preserve distinct media.
        if shot.get('assetId') and result.get('assetId') and shot['assetId'] != result['assetId']:
            continue
        for field in ('assetId', 'assetUrl', 'mediaWidth', 'mediaHeight', 'generationStatus'):
            if result.get(field) is not None and not shot.get(field):
                shot[field] = result[field]
        aliases[result['id']] = shot['id']
        removed.add(result['id'])
        canonical.pop(key)

    for key, character in list(canonical.items()):
        if not key.startswith('character:'):
            continue
        visual = canonical.get(key.split(':', 1)[1])
        if not visual or visual.get('type') not in ('asset', 'character'):
            continue
        if not visual.get('assetId') or visual.get('assetId') != character.get('assetId'):
            continue
        was_character = visual.get('type') == 'character'
        visual['type'] = 'character'
        for field in ('characterId', 'characterName', 'description', 'tone', 'assetUrl'):
            if field in character and (not was_character or field not in visual):
                visual[field] = character[field]
        aliases[character['id']] = visual['id']
        removed.add(character['id'])
        canonical.pop(key)

    source, working = canonical.get('source'), canonical.get('working')
    if (source and working and isinstance(run.get('source'), str) and isinstance(run.get('working_story'), str)
            and _prompt(source) == run['source'] and _prompt(working) == run['working_story']):
        aliases[source['id']] = working['id']
        removed.add(source['id'])
        canonical.pop('source')

    graph['nodes'] = [n for n in graph['nodes'] if n['id'] not in removed]
    remaining_ids = {n['id'] for n in graph['nodes']}
    edges, seen = [], set()
    for edge in graph['edges']:
        if edge.get('relation') == 'agent-flow' and (
                edge.get('agent_run_id') == rid or edge.get('from') in owned_ids or edge.get('to') in owned_ids):
            continue
        source_id = aliases.get(edge.get('from'), edge.get('from'))
        target_id = aliases.get(edge.get('to'), edge.get('to'))
        if ((source_id not in remaining_ids and edge.get('from') in removed)
                or (target_id not in remaining_ids and edge.get('to') in removed)
                or source_id == target_id):
            continue
        # Ordinary canvas connections have one edge for each pair. Keep any
        # port/relation metadata distinct when importing richer user graphs.
        signature = (source_id, target_id, edge.get('relation'), edge.get('fromPort'), edge.get('toPort'))
        if signature in seen:
            continue
        seen.add(signature)
        if source_id != edge.get('from') or target_id != edge.get('to'):
            edge = {**edge, 'from': source_id, 'to': target_id}
        edges.append(edge)
    graph['edges'] = edges

    movable, fixed = [], []
    canonical_ids = {n['id'] for n in canonical.values()}
    for node in graph['nodes']:
        if node['id'] not in canonical_ids:
            fixed.append(node)
            continue
        baseline = baselines[node['id']]
        at_baseline = all(abs(node.get(axis, 0)-baseline.get(axis, 0)) < 1 for axis in ('x', 'y'))
        if node.get('agent_layout_pending') or at_baseline:
            movable.append(node)
        else:
            fixed.append(node)
        node['agent_layout'] = {**baseline, 'version': LAYOUT_VERSION}
        node.pop('agent_layout_pending', None)

    def overlaps(x, y, other):
        width = max(NODE_WIDTH, float(other.get('width') or 0))
        height = max(NODE_HEIGHT, float(other.get('height') or 0))
        return (x < other.get('x', 0)+width+40 and x+NODE_WIDTH+40 > other.get('x', 0)
                and y < other.get('y', 0)+height+40 and y+NODE_HEIGHT+40 > other.get('y', 0))

    obstacles = list(fixed)
    column_x = 100
    for column in range(5):
        members = [n for key, n in canonical.items() if column_for(key) == column]
        if not members:
            continue
        columns = min(3, max(1, math.ceil(len(members)/4))) if column in (1, 3) else 1
        rows = max(1, math.ceil(len(members)/columns))
        automatic = sorted((n for n in movable if column_for(n['agent_key']) == column), key=lambda n: order_for(n['agent_key']))
        for index, node in enumerate(automatic):
            x, y = column_x+(index//rows)*STEP_X, 160+(index % rows)*STEP_Y
            while any(overlaps(x, y, other) for other in obstacles):
                y += STEP_Y
            node.update(x=x, y=y, agent_layout={'x': x, 'y': y, 'version': LAYOUT_VERSION})
            obstacles.append(node)
        column_x += columns*STEP_X
    return before != (graph['nodes'], graph['edges'])
