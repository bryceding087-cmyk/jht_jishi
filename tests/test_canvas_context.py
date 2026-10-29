import json
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from canvas_context import canvas_context


class CanvasContextTests(unittest.TestCase):
    def host(self, graph, assets=None, tasks=None):
        def asset(aid):
            if aid not in (assets or {}):
                raise HTTPException(404, '不可访问')
            return assets[aid]
        return SimpleNamespace(active_project=lambda pid: graph, require_live_asset=asset,
                               listing=lambda kind: (tasks or {}).get(kind, []))

    def test_empty_bound_canvas_is_a_project_and_unbound_chat_is_not(self):
        graph = {'id': 'p', 'name': '未命名画布', 'revision': 3, 'nodes': [], 'edges': [], 'groups': []}
        host = self.host(graph)
        context = canvas_context(host, {'creation_type': 'canvas', 'project_id': 'p'})
        self.assertTrue(context['available'])
        self.assertEqual(context['project']['name'], '未命名画布')
        self.assertEqual(context['summary']['node_count'], 0)
        self.assertFalse(canvas_context(host, {'creation_type': 'conversation', 'project_id': 'conversation'})['available'])

    def test_metadata_tasks_and_references_are_allowlisted_and_scoped(self):
        graph = {'id': 'p', 'name': '测试', 'nodes': [{'id': 'n', 'type': 'asset', 'assetId': 'owned',
            'image_settings': {'prompt': '窗前', 'image_task_id': 'image', 'reference_asset_ids': ['denied'], 'api_key': 'SECRET'}}],
            'edges': [], 'groups': []}
        host = self.host(graph, {'owned': {'id': 'owned', 'name': '参考图.png', 'kind': 'image', 'url': 'SIGNED_SECRET', 'api_key': 'SECRET'}},
            {'image_task': [{'id': 'image', 'project_id': 'p', 'status': 'queued', 'queue_state': 'waiting'},
                            {'id': 'other', 'project_id': 'other', 'node_id': 'n', 'status': 'succeeded', 'created_at': 100}]})
        context = canvas_context(host, {'studio': {'canvas_project_id': 'p'}})
        node = context['nodes'][0]
        self.assertEqual(node['generation']['status'], 'queued')
        self.assertEqual(node['assets'][0]['name'], '参考图.png')
        self.assertEqual(node['assets'][1], {'id': 'denied', 'available': False})
        self.assertNotIn('SECRET', json.dumps(context))

    def test_large_graph_is_bounded_selected_nodes_first_and_counts_exact(self):
        nodes = [{'id': str(i), 'type': 'prompt', 'text': '内容' * 1200} for i in range(1600)]
        graph = {'id': 'p', 'name': '大画布', 'nodes': nodes, 'edges': [{'from': str(i), 'to': str(i + 1)} for i in range(1599)],
                 'groups': [{'id': 'g', 'title': '组', 'node_ids': ['1599']}]}
        context = canvas_context(self.host(graph), {'studio': {'canvas_project_id': 'p'}}, {'selected_group_id': 'g'})
        self.assertEqual(context['nodes'][0]['id'], '1599')
        self.assertEqual(context['summary']['node_count'], 1600)
        self.assertGreater(context['omitted_details']['nodes'], 0)
        self.assertLess(len(json.dumps(context, ensure_ascii=False)), 34000)

    def test_deleted_or_forbidden_graph_is_not_exposed(self):
        def denied(pid):
            raise HTTPException(404, '不存在')
        host = SimpleNamespace(active_project=denied)
        self.assertFalse(canvas_context(host, {'studio': {'canvas_project_id': 'foreign'}})['available'])


if __name__ == '__main__':
    unittest.main()
