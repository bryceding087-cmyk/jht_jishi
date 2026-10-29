"""Batch prompt transport with per-node validation and durable request identity."""
from copy import deepcopy
import json
from threading import Lock
import time

from fastapi import HTTPException

from studio_concurrency import mark_text_repair, run_bounded, text_request_slot
from studio_graph import digest
from studio_prompt_optimizer import OPTIMIZER_PROMPT, apply_direction, validate_direction


BATCH_SIZE = 6
MAX_BATCH_CHARS = 30000
# The per-node semantic contract and digest remain unchanged. This wrapper only
# changes transport and output addressing, so old node checkpoints stay valid.
BATCH_PROMPT = OPTIMIZER_PROMPT.rsplit('只返回 JSON', 1)[0].replace(
    '对 task=optimize_node_prompt 的一个生成节点',
    '对 task=optimize_node_prompts 中 nodes 的每个生成节点') + '''
shared 中的字段适用于所有节点；节点同名字段优先。shared.assets 为本批资产定义，节点 production.asset_keys 指定仅与该节点相关的资产。
分别检查每个节点的 remaining_chars、模式和约束，不把其他节点的角色或动作混入。每个输入节点只输出一项，node_id 原样返回。
node_id 仅为宿主回填定位字段；direction 内仍不得出现任何新的编号或引用标签。
只返回 JSON {"items":[{"node_id":"原始节点编号","direction":"该节点的补充执行指导"}]}，不得返回其他字段或解释。'''


def batch_context(items, *, errors=None):
    """Send common style, rules and asset definitions once, not once per node."""
    contexts = [deepcopy(item[1]) for item in items]
    shared = {}
    for key in ('media_type', 'visual_style', 'text_model', 'skill_revision', 'model'):
        if contexts and all(context.get(key) == contexts[0].get(key) for context in contexts):
            shared[key] = contexts[0].get(key)
            for context in contexts:
                context.pop(key, None)
    assets = {}
    for context in contexts:
        context.pop('task', None)
        production = context.get('production', {})
        keys = []
        for asset in production.pop('assets', []):
            # Assets come from the same frozen production catalog. Include the
            # content digest in the transport key if two variants share a key.
            key = str(asset.get('key') or digest(asset))
            if key in assets and assets[key] != asset:
                key += ':' + digest(asset)[:12]
            assets[key] = asset
            keys.append(key)
        production['asset_keys'] = keys
        context['production'] = production
    for key in ('style_bible', 'constraints'):
        if contexts and all(c['production'].get(key) == contexts[0]['production'].get(key) for c in contexts):
            shared.setdefault('production', {})[key] = contexts[0]['production'].get(key)
            for context in contexts:
                context['production'].pop(key, None)
    shared['assets'] = assets
    result = {'task': 'optimize_node_prompts', 'shared': shared, 'nodes': contexts}
    if errors:
        result['repair'] = {'errors': errors,
                            'instruction': '只修正本批列出的失败节点；已通过的其他节点已保存，不要重复输出。'}
    return result


def group_batches(items):
    """Bound both node count and serialized prompt size without truncation."""
    groups = {}
    for item in items:
        key = (item[1]['media_type'], item[2]['metadata']['version'])
        groups.setdefault(key, []).append(item)
    batches = []
    for group in groups.values():
        pending = []
        for item in group:
            candidate = [*pending, item]
            size = len(json.dumps(batch_context(candidate), ensure_ascii=False)) + len(BATCH_PROMPT) + len(item[2]['system'])
            if pending and (len(candidate) > BATCH_SIZE or size > MAX_BATCH_CHARS):
                batches.append(pending)
                pending = []
                candidate = [item]
                size = len(json.dumps(batch_context(candidate), ensure_ascii=False)) + len(BATCH_PROMPT) + len(item[2]['system'])
            if size > MAX_BATCH_CHARS:
                raise HTTPException(422, f'「{item[0]["title"]}」的优化上下文过长，请精简相关资产描述或拆分片段；原文未截断')
            pending = candidate
        if pending:
            batches.append(pending)
    return batches


def validate_batch(value, items):
    """Keep independently valid siblings when another item is missing or bad."""
    expected = {item[0]['id']: item[1] for item in items}
    if not isinstance(value, dict) or set(value) != {'items'} or not isinstance(value.get('items'), list):
        return {}, {nid: '请只返回 items 数组，每项包含 node_id 和 direction。' for nid in expected}
    rows = {}
    for row in value['items']:
        # An unsolicited result can never target a different node. Keep valid
        # siblings instead of billing another full batch for an extra row.
        if isinstance(row, dict) and isinstance(row.get('node_id'), str) and row['node_id'] in expected:
            rows.setdefault(row['node_id'], []).append(row)
    directions, errors = {}, {}
    for nid, context in expected.items():
        matches = rows.get(nid, [])
        if len(matches) != 1:
            errors[nid] = '该节点缺少结果。' if not matches else '该节点重复返回，请仅保留一个结果。'
            continue
        row = matches[0]
        if set(row) != {'node_id', 'direction'}:
            errors[nid] = '每项只能包含 node_id 和 direction，不能修改节点参数。'
            continue
        try:
            directions[nid] = validate_direction({'direction': row['direction']}, context)
        except (HTTPException, ValueError, TypeError) as error:
            errors[nid] = str(getattr(error, 'detail', error))
    return directions, errors


def _operation_id(service, rid, key):
    epoch = service.get(rid).get('epochs', {}).get(key.split(':')[0], 0)
    return f'{rid}:op:{key}:{epoch}'


def _receipt(service, rid, key):
    return service.h.raw_record('agent_operation', _operation_id(service, rid, key))


def _request(service, rid, key, label, system, context, on_request=None):
    """Journal the received response before validating any individual node."""
    run = service.get(rid)
    model = service.h.internal_registry_model('text', run['preferences']['models'].get('text') or None)

    def call(_):
        try:
            if on_request:
                on_request()
            response = service.h.llm_json(model, [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}])
        except Exception as error:
            from agent_runtime import UnknownResult
            message = service.h.provider_error_message(error)
            if model.get('api_key'):
                message = message.replace(model['api_key'], '[已隐藏密钥]')
            if '超时' in message or 'timed out' in message.lower():
                raise UnknownResult(504, message + ' 本次批量文本请求未自动重发。') from None
            raise HTTPException(502, message) from None
        try:
            value = service.h.parse_llm_json(response)
            if isinstance(value, dict):
                return value
        except (RuntimeError, ValueError):
            pass
        # An unusable envelope repairs this batch; valid JSON is assessed per
        # node. Do not retain arbitrary extra raw provider text in the run.
        return {'_batch_invalid_json': True}

    with text_request_slot(run['owner_id'], model, lambda: service.checkpoint(rid)):
        return service.operation(rid, key, label, call, defer_completion=True)


def optimize_batches(service, rid, graph, prepared):
    started = time.monotonic()
    by_id = {item[0]['id']: item for item in prepared}
    # Recover valid node checkpoints before sizing transport. A node with an
    # older, larger context needs no new request when its exact result exists.
    # group_batches below still preflights all uncached requests before dispatch.
    run = service.get(rid)
    studio = run['studio']
    manifests = deepcopy(studio.get('prompt_batches', {}))
    valid = {}
    repaired_nodes = set()
    repair_requests = 0
    state, state_lock = {}, Lock()
    batches = []

    # Old per-node calls and batch calls share the same no-unknown-replay rule.
    # Check the whole run so changing grouping, mode or job cannot hide one.
    for op in service.h.listing('agent_operation'):
        if op.get('run_id') != rid:
            continue
        key = op.get('key', '')
        if ':optimize:' not in key and not key.startswith('studio-prompt-batch:'):
            continue
        if op.get('status') in ('unknown', 'submitting'):
            service.patch(rid, retry_warning='文本请求结果未知，重新请求可能再次计费。请先核对服务商记录。')
            raise HTTPException(409, '上次提示词请求结果未知，继续查询不会重新提交。核对服务商记录后，可主动重新请求；可能再次计费。')

    def save_directions(directions):
        if not directions:
            return
        additions = {}
        for nid, direction in directions.items():
            if nid not in by_id:
                continue
            node, _, bundle, input_digest = by_id[nid]
            apply_direction(node, direction, bundle['metadata'], input_digest)
            valid[nid] = direction
            additions[nid] = {'inputDigest': input_digest, 'direction': direction,
                             'skills': bundle['metadata']['skills'], 'version': bundle['metadata']['version']}
        with service.lock:
            latest = service.get(rid)['studio'].get('prompt_optimizations', {})
            service.studio_patch(rid, prompt_optimizations={**latest, **additions})

    for nid, item in by_id.items():
        cached = studio.get('prompt_optimizations', {}).get(nid, {})
        if cached.get('inputDigest') == item[3]:
            valid[nid] = validate_direction({'direction': cached.get('direction')}, item[1])
    save_directions(dict(valid))

    # A pause can occur after the journal receives an answer but before the
    # coordinator caches it. Recover those known replies, including old single
    # node receipts, without paying again or requiring the old graph to exist.
    for op in service.h.listing('agent_operation'):
        if op.get('run_id') != rid or not isinstance(op.get('result'), dict):
            continue
        for nid, item in by_id.items():
            if nid in valid or ':optimize:' + item[3] not in op.get('key', ''):
                continue
            try:
                direction = validate_direction(op['result'], item[1])
            except (HTTPException, ValueError, TypeError):
                continue
            save_directions({nid: direction})
    for manifest in manifests.values():
        matching = [by_id[m['node_id']] for m in manifest['members']
                    if m['node_id'] in by_id and by_id[m['node_id']][3] == m['input_digest']]
        if not matching:
            continue
        for phase in ('initial', 'repair'):
            op = _receipt(service, rid, manifest['key'] + ':' + phase)
            if op and isinstance(op.get('result'), dict):
                directions, _ = validate_batch(op['result'], matching)
                save_directions({nid: direction for nid, direction in directions.items() if nid not in valid})

    assigned = set(valid)
    for batch_id, manifest in reversed(list(manifests.items())):
        members = manifest['members']
        if not all(m['node_id'] in by_id and by_id[m['node_id']][3] == m['input_digest'] for m in members):
            continue
        if not any(m['node_id'] not in assigned for m in members):
            continue
        items = [by_id[m['node_id']] for m in members]
        for phase in ('initial', 'repair'):
            op = _receipt(service, rid, manifest['key'] + ':' + phase)
            if op and op['status'] in ('failed', 'invalid'):
                raise HTTPException(409, '部分节点提示词尚未通过检查，请点击“重试失败步骤”；已完成的优化会保留。')
        batches.append({'id': batch_id, 'items': items, 'manifest': manifest})
        assigned.update(m['node_id'] for m in members)
    for items in group_batches([item for item in prepared if item[0]['id'] not in assigned]):
        members = [{'node_id': item[0]['id'], 'input_digest': item[3]} for item in items]
        batch_id = digest([members, BATCH_PROMPT, items[0][2]['metadata']])
        manifest = {'members': members, 'key': 'studio-prompt-batch:' + batch_id,
                    'kind': items[0][1]['media_type'], 'created_at': time.time()}
        manifests[batch_id] = manifest
        batches.append({'id': batch_id, 'items': items, 'manifest': manifest})
    service.studio_patch(rid, stage='prompt-optimization', prompt_batches=manifests)

    def publish(value=None):
        value = value or {}
        with state_lock:
            running_batches = [batch for batch in batches if state.get(batch['id']) == 'running']
            running = sum(sum(item[0]['id'] not in valid for item in batch['items']) for batch in running_batches)
            failed = sum(sum(item[0]['id'] not in valid for item in batch['items'])
                         for batch in batches if state.get(batch['id']) == 'failed')
        complete = sum(all(item[0]['id'] in valid for item in batch['items']) for batch in batches)
        status = value.get('status', 'running')
        if status == 'completed' and len(valid) != len(prepared):
            status = 'running'
        service.studio_patch(rid, prompt_optimization_progress={
            'current': len(valid), 'total': len(prepared), 'running': running,
            'queued': max(0, len(prepared) - len(valid) - running - failed),
            'failed': failed, 'repaired': repair_requests, 'repaired_nodes': len(repaired_nodes),
            'elapsed_seconds': int(time.monotonic() - started),
            'concurrency': 3, 'status': status, 'batch_current': complete,
            'batch_total': len(batches), 'batch_size': BATCH_SIZE, 'running_batches': len(running_batches),
            'title': '素材与镜头提示词优化完成' if status == 'completed' else '正在批量优化素材与镜头提示词'})

    def execute(batch):
        with state_lock:
            state[batch['id']] = 'running'
        phase = batch.get('phase', 'initial')
        items = batch.get('request_items', batch['items'])
        key = batch['manifest']['key'] + ':' + phase
        def requested():
            nonlocal repair_requests
            if phase == 'repair':
                mark_text_repair()
                with state_lock:
                    repair_requests += 1
        try:
            context = batch_context(items, errors=batch.get('errors'))
            raw = _request(service, rid, key, f'优化提示词 · {len(items)} 个节点' + ('（局部修正）' if phase == 'repair' else ''),
                           BATCH_PROMPT + '\n\n' + items[0][2]['system'], context, on_request=requested)
            return validate_batch(raw, items)
        except BaseException as error:
            with state_lock:
                state[batch['id']] = 'paused' if type(error).__name__ == 'StopWork' else 'failed'
            raise

    def commit(batch, result):
        directions, errors = result
        save_directions({nid: direction for nid, direction in directions.items() if nid not in valid})
        errors = {nid: error for nid, error in errors.items() if nid not in valid}
        phase = batch.get('phase', 'initial')
        if phase == 'repair':
            repaired_nodes.update(directions)
        batch['manifest']['errors'] = errors
        with service.lock:
            latest = service.get(rid)['studio'].get('prompt_batches', {})
            service.studio_patch(rid, prompt_batches={**latest, batch['id']: batch['manifest']})
        with state_lock:
            state[batch['id']] = 'failed' if errors and phase == 'repair' else 'done'
        key = batch['manifest']['key'] + ':' + phase
        service.event(rid, 'step', f'已保存 {len(directions)} 个节点的提示词优化',
                      step_id=_operation_id(service, rid, key), status='failed' if errors and phase == 'repair' else 'done')
        if errors and phase == 'repair':
            op = _receipt(service, rid, key)
            if op:
                service.h.save('agent_operation', {**op, 'status': 'invalid', 'error': json.dumps(errors, ensure_ascii=False)})
            raise HTTPException(422, f'还有 {len(errors)} 个节点的提示词未通过检查。已完成的优化已保留，请重试失败步骤。')

    if batches:
        run_bounded(batches, execute, commit, checkpoint=lambda: service.checkpoint(rid), progress=publish)
    repairs = []
    for batch in batches:
        remaining = [item for item in batch['items'] if item[0]['id'] not in valid]
        if not remaining:
            continue
        # A repair's member list is saved before dispatch. An unknown receipt
        # is blocked globally; only an explicit retry can start another attempt
        # with a smaller list after a partially successful previous attempt.
        errors = batch['manifest'].get('errors', {})
        batch['manifest']['repair_members'] = [item[0]['id'] for item in remaining]
        repairs.append({**batch, 'phase': 'repair', 'request_items': remaining, 'errors': errors})
    if repairs:
        with service.lock:
            latest = service.get(rid)['studio'].get('prompt_batches', {})
            service.studio_patch(rid, prompt_batches={**latest, **{b['id']: b['manifest'] for b in repairs}})
        run_bounded(repairs, execute, commit, checkpoint=lambda: service.checkpoint(rid), progress=publish)
    service.checkpoint(rid)
    publish({'status': 'completed'})
    return graph
