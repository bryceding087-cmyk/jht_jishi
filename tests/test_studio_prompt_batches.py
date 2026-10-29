"""Batch transport keeps per-node contracts and never hides unknown receipts."""
from copy import deepcopy
import json
from threading import RLock
from types import SimpleNamespace
import unittest

from fastapi import HTTPException

from agent_runtime import AgentService, StopWork
from agent_studio import StudioAgentMixin
from studio_graph import digest
from studio_prompt_batches import BATCH_SIZE, MAX_BATCH_CHARS, BATCH_PROMPT, batch_context, group_batches, validate_batch
from studio_prompt_optimizer import OPTIMIZER_PROMPT, optimization_context, optimization_digest, production_context
from studio_skills import load_skill_bundle


DIRECTION = '保持既定人物身份、造型与机位，清晰呈现材质、轮廓和空间关系。'


def graph_nodes(count):
    return {'nodes': [{'id': f'image-{index}', 'type': 'asset', 'title': f'角色 {index}',
        'image_settings': {'prompt': '清晰呈现既定人物。', 'model_config_id': 'image'},
        'mujian': {'role': 'reference', 'inputs': []}} for index in range(count)]}


def prepared_nodes(graph, studio=None):
    bundle = load_skill_bundle('image')
    prepared = []
    for node in graph['nodes']:
        context = optimization_context(node, {'id': 'image', 'base_url': 'https://batch.invalid'},
            {'bundle': bundle['metadata'], 'contract': digest(OPTIMIZER_PROMPT)},
            production=production_context(node, graph, studio or {}),
            text_model={'id': 'text', 'base_url': 'https://batch.invalid'})
        context['task'] = 'optimize_node_prompt'
        prepared.append((node, context, bundle, optimization_digest(context)))
    return prepared


class MemoryBatchService(StudioAgentMixin):
    operation = AgentService.operation
    checkpoint = AgentService.checkpoint

    def __init__(self, provider=None):
        self.lock = RLock()
        self.calls, self.events, self.records = [], [], {}
        self.run = {'id': 'run', 'owner_id': 'batch-account', 'status': 'running',
                    'studio_job': {'id': 'job'}, 'epochs': {},
                    'preferences': {'models': {'text': 'text'}}, 'studio': {}}
        self.provider = provider
        self.h = SimpleNamespace(
            internal_registry_model=lambda kind, mid: {'id': mid, 'base_url': 'https://batch.invalid'},
            llm_json=self.call_provider, parse_llm_json=lambda value: value,
            provider_error_message=str, raw_record=self.raw_record, save=self.save, listing=self.listing)

    def get(self, rid):
        with self.lock:
            return deepcopy(self.run)

    def patch(self, rid, **fields):
        with self.lock:
            self.run.update(deepcopy(fields))
            return deepcopy(self.run)

    def event(self, rid, kind, text, **fields):
        with self.lock:
            self.events.append({'kind': kind, 'text': text, **fields})

    def raw_record(self, kind, key):
        with self.lock:
            return deepcopy(self.records.get((kind, key)))

    def save(self, kind, value):
        with self.lock:
            self.records[(kind, value['id'])] = deepcopy(value)
        return deepcopy(value)

    def listing(self, kind):
        with self.lock:
            return deepcopy([value for (record_kind, _), value in self.records.items() if record_kind == kind])

    def call_provider(self, model, messages):
        context = json.loads(messages[-1]['content'])
        with self.lock:
            self.calls.append(deepcopy(context))
        if self.provider:
            return self.provider(context)
        return {'items': [{'node_id': node['node_id'], 'direction': DIRECTION} for node in context['nodes']]}

    def explicit_retry(self):
        with self.lock:
            for record in self.records.values():
                if record['status'] in ('failed', 'invalid', 'unknown'):
                    record.update(status='retry', attempt=record.get('attempt', 0) + 1)
            self.run.update(status='running', stop_requested=False)


class StudioPromptBatchTests(unittest.TestCase):
    def test_batch_size_shared_context_and_all_node_outputs(self):
        service = MemoryBatchService()
        graph = graph_nodes(19)
        original = deepcopy(graph)
        service.studio_optimize_prompts('run', graph)
        self.assertEqual(sorted(len(call['nodes']) for call in service.calls), [1, 6, 6, 6])
        self.assertTrue(all(call['task'] == 'optimize_node_prompts' for call in service.calls))
        self.assertTrue(all('skill_revision' in call['shared'] for call in service.calls))
        self.assertTrue(all('skill_revision' not in node for call in service.calls for node in call['nodes']))
        self.assertEqual(set(service.run['studio']['prompt_optimizations']), {node['id'] for node in graph['nodes']})
        progress = service.run['studio']['prompt_optimization_progress']
        self.assertEqual((progress['current'], progress['total'], progress['batch_current'], progress['batch_total']), (19, 19, 4, 4))
        self.assertEqual(progress['running_batches'], 0)
        service.studio_optimize_prompts('run', original)
        self.assertEqual(len(service.calls), 4)
        self.assertEqual(original, graph)

    def test_partial_repair_only_sends_failed_nodes_and_retry_keeps_successes(self):
        def provider(context):
            ids = [node['node_id'] for node in context['nodes']]
            if not context.get('repair'):
                return {'items': [
                    {'node_id': nid, 'direction': DIRECTION if nid != 'image-1' else '新增@错误参考'}
                    for nid in ids if nid != 'image-2'] + [{'node_id': 'image-3', 'direction': DIRECTION}]}
            return {'items': [{'node_id': nid, 'direction': '新增@错误参考' if nid == 'image-3' else DIRECTION} for nid in ids]}
        service = MemoryBatchService(provider)
        graph = graph_nodes(6)
        with self.assertRaises(HTTPException):
            service.studio_optimize_prompts('run', deepcopy(graph))
        self.assertEqual([len(call['nodes']) for call in service.calls], [6, 3])
        self.assertEqual({n['node_id'] for n in service.calls[1]['nodes']}, {'image-1', 'image-2', 'image-3'})
        self.assertEqual(set(service.run['studio']['prompt_optimizations']), {'image-0', 'image-1', 'image-2', 'image-4', 'image-5'})
        calls_before = len(service.calls)
        with self.assertRaises(HTTPException):
            service.studio_optimize_prompts('run', deepcopy(graph))
        self.assertEqual(len(service.calls), calls_before)
        service.explicit_retry()
        service.provider = None
        service.studio_optimize_prompts('run', deepcopy(graph))
        self.assertEqual([n['node_id'] for n in service.calls[-1]['nodes']], ['image-3'])
        self.assertEqual(len(service.calls), 3)
        self.assertEqual(len(service.run['studio']['prompt_optimizations']), 6)

    def test_unknown_response_cannot_be_bypassed_by_regrouping_or_new_job(self):
        def timeout(context):
            raise RuntimeError('provider timed out')
        service = MemoryBatchService(timeout)
        graph = graph_nodes(6)
        with self.assertRaises(HTTPException):
            service.studio_optimize_prompts('run', deepcopy(graph))
        self.assertEqual(len(service.calls), 1)
        first_ids = [node['node_id'] for node in service.calls[0]['nodes']]
        service.run['studio_job'] = {'id': 'new-job'}
        changed = graph_nodes(7)
        changed['nodes'].reverse()
        with self.assertRaisesRegex(HTTPException, '409'):
            service.studio_optimize_prompts('run', deepcopy(changed))
        self.assertEqual(len(service.calls), 1)
        service.explicit_retry()
        service.provider = None
        service.studio_optimize_prompts('run', changed)
        self.assertIn(first_ids, [[node['node_id'] for node in call['nodes']] for call in service.calls[1:]])
        self.assertEqual(len(service.run['studio']['prompt_optimizations']), 7)

    def test_pause_after_receiving_batch_reuses_journal_without_new_request(self):
        service = MemoryBatchService()
        def pause(context):
            service.run['stop_requested'] = True
            return {'items': [{'node_id': node['node_id'], 'direction': DIRECTION} for node in context['nodes']]}
        service.provider = pause
        graph = graph_nodes(6)
        with self.assertRaises(StopWork):
            service.studio_optimize_prompts('run', deepcopy(graph))
        self.assertEqual(len(service.calls), 1)
        self.assertTrue(all(op['status'] == 'succeeded' for op in service.listing('agent_operation')))
        service.run['stop_requested'] = False
        service.provider = None
        service.studio_optimize_prompts('run', graph)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(len(service.run['studio']['prompt_optimizations']), 6)

    def test_old_single_node_digest_and_known_receipts_are_reused(self):
        service = MemoryBatchService()
        graph = graph_nodes(3)
        prepared = prepared_nodes(graph)
        service.run['studio']['prompt_optimizations'] = {'image-0': {
            'inputDigest': prepared[0][3], 'direction': DIRECTION}}
        for item in prepared[1:]:
            key = 'studio:old-job:optimize:' + item[3]
            service.save('agent_operation', {'id': f'run:op:{key}:0', 'run_id': 'run', 'key': key,
                'status': 'succeeded', 'result': {'direction': DIRECTION}})
        service.studio_optimize_prompts('run', graph)
        self.assertFalse(service.calls)
        self.assertEqual(len(service.run['studio']['prompt_optimizations']), 3)

    def test_oversized_cached_context_is_reused_without_transport_preflight(self):
        service = MemoryBatchService()
        graph = graph_nodes(1)
        graph['nodes'][0]['mujian']['sources'] = [{'line': 1, 'quote': '原' * MAX_BATCH_CHARS}]
        item = prepared_nodes(graph)[0]
        service.run['studio']['prompt_optimizations'] = {'image-0': {
            'inputDigest': item[3], 'direction': DIRECTION}}
        service.studio_optimize_prompts('run', graph)
        self.assertFalse(service.calls)
        self.assertEqual(service.run['studio']['prompt_optimization_progress']['current'], 1)
        self.assertTrue(graph['nodes'][0]['image_settings']['prompt'].endswith(DIRECTION))

    def test_all_uncached_requests_are_preflighted_before_any_model_call(self):
        service = MemoryBatchService()
        graph = graph_nodes(8)
        graph['nodes'][-1]['mujian']['sources'] = [{'line': 1, 'quote': '原' * MAX_BATCH_CHARS}]
        with self.assertRaises(HTTPException):
            service.studio_optimize_prompts('run', graph)
        self.assertFalse(service.calls)
        self.assertFalse(service.listing('agent_operation'))

    def test_character_budget_splits_without_truncating_node_context(self):
        graph = graph_nodes(6)
        prepared = prepared_nodes(graph)
        for item in prepared:
            item[1]['sources'] = [{'quote': '原' * 6000}]
        groups = group_batches(prepared)
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(len(group) for group in groups), 6)
        for group in groups:
            context = batch_context(group)
            self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)) + len(BATCH_PROMPT) + len(group[0][2]['system']), MAX_BATCH_CHARS)
            self.assertTrue(all(len(node['sources'][0]['quote']) == 6000 for node in context['nodes']))

    def test_out_of_scope_output_never_creates_or_modifies_another_node(self):
        items = prepared_nodes(graph_nodes(2))
        directions, errors = validate_batch({'items': [
            {'node_id': 'image-0', 'direction': DIRECTION},
            {'node_id': 'image-1', 'direction': DIRECTION},
            {'node_id': 'foreign-node', 'direction': '修改其他项目'}]}, items)
        self.assertEqual(set(directions), {'image-0', 'image-1'})
        self.assertFalse(errors)

    def test_unknown_partial_repair_keeps_good_nodes_and_blocks_new_grouping(self):
        service = MemoryBatchService()
        def provider(context):
            if context.get('repair'):
                raise RuntimeError('读取超时')
            return {'items': [{'node_id': node['node_id'],
                'direction': '新增@错误引用' if node['node_id'] == 'image-2' else DIRECTION}
                for node in context['nodes']]}
        service.provider = provider
        graph = graph_nodes(6)
        with self.assertRaises(HTTPException):
            service.studio_optimize_prompts('run', deepcopy(graph))
        self.assertEqual(len(service.run['studio']['prompt_optimizations']), 5)
        self.assertEqual([len(call['nodes']) for call in service.calls], [6, 1])
        with self.assertRaises(HTTPException):
            service.studio_optimize_prompts('run', graph_nodes(7))
        self.assertEqual(len(service.calls), 2)
        service.explicit_retry()
        service.provider = None
        service.studio_optimize_prompts('run', graph)
        self.assertEqual([node['node_id'] for node in service.calls[-1]['nodes']], ['image-2'])
        self.assertEqual(len(service.calls), 3)


if __name__ == '__main__':
    unittest.main()
