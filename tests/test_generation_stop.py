"""Stop submitted and not-yet-submitted requests through the real ASGI API."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from test_conversation_generation import ConversationGenerationTests as Fixture


class GenerationStopTests(unittest.TestCase):
    setUpClass = Fixture.__dict__['setUpClass']
    tearDownClass = Fixture.__dict__['tearDownClass']
    for name in ('login', 'request', 'upload', 'owned_save', 'fixture_setup', 'setUp', 'tearDown',
                 'conversation', 'payload', 'route', 'submit', 'finish', 'state'):
        locals()[name] = getattr(Fixture, name)

    def stop(self, kind, browser=None, expected=200, **fields):
        return self.request(browser or self.a, 'POST', '/api/generation/stop', {'kind': kind, **fields}, expected)

    def test_stop_before_creation_is_durable_and_owner_scoped(self):
        result = self.stop('image', request_id='before-context')
        self.assertEqual(result['status'], 'pending_submission')
        rejected = self.request(self.a, 'POST', '/api/conversations', {'title': '停止', 'kind': 'image',
            'prompt': '测试', 'request_id': 'before-context'}, 409)
        self.assertEqual(rejected['detail']['code'], 'generation_request_stopped')
        self.assertFalse(self.h.listing('conversation'))
        self.assertFalse(self.h.listing('image_task'))
        foreign = self.conversation(self.b, 'image', 'before-context')
        self.submit(self.b, 'image', foreign, 'before-context')
        fresh = self.conversation(self.a, 'image', 'fresh')
        self.submit(self.a, 'image', fresh, 'fresh')

    def test_queued_stop_is_precise_keeps_siblings_and_blocks_replay(self):
        context = self.conversation(self.a, 'image', 'queue')
        first = self.submit(self.a, 'image', context, 'first')
        second = self.submit(self.a, 'image', context, 'second')
        result = self.stop('image', request_id='first', conversation_id=context['id'])
        self.assertEqual(result['status'], 'stopped')
        self.assertFalse(result['provider_may_continue'])
        task = self.h.get('image_task', first['id'])
        self.assertEqual(task['status'], 'cancelled')
        self.assertEqual(self.h.generation_queue.job('image_task', first['id'])['state'], 'cancelled')
        self.assertEqual(self.state(self.a, context)['task']['id'], second['id'])
        self.assertEqual(self.submit(self.a, 'image', context, 'first', 409)['detail']['code'], 'generation_request_stopped')
        self.assertEqual(self.stop('image', task_id=first['id'])['tasks'][0]['id'], first['id'])

    def test_executing_stop_releases_local_busy_without_faking_provider_cancel(self):
        context = self.conversation(self.a, 'video', 'running')
        task = self.submit(self.a, 'video', context, 'running')
        self.h.update_task(task['id'], status='running', provider_task_id='upstream-original')
        self.h.generation_queue.patch(self.h.generation_queue.job('task', task['id']), state='provider')
        result = self.stop('video', task_id=task['id'], project_id=context['id'], node_id='conversation-video')
        self.assertTrue(result['provider_may_continue'])
        self.assertEqual(result['status'], 'stop_requested')
        saved = self.h.get('task', task['id'])
        self.assertEqual(saved['status'], 'running')
        self.assertEqual(saved['provider_task_id'], 'upstream-original')
        self.assertEqual(self.h.generation_queue.job('task', task['id'])['state'], 'provider')
        self.assertFalse(self.state(self.a, context)['is_busy'])
        self.assertIsNone(self.request(self.a, 'GET', '/api/conversations')[0]['active_task'])
        with patch.object(self.h, 'refresh_video_task') as refresh:
            self.request(self.a, 'GET', '/api/conversations/' + context['id'])
            self.h.manual_generation.recover(context['id'])
            refresh.assert_not_called()

    def test_foreign_and_wrong_node_stops_have_no_side_effects(self):
        task = self.submit(self.a, 'image', self.pa, 'private')
        self.stop('image', browser=self.b, task_id=task['id'], expected=404)
        self.stop('image', browser=self.b, request_id='private', project_id=self.pa['id'], expected=404)
        self.stop('image', task_id=task['id'], node_id='wrong', expected=404)
        self.assertFalse(self.h.get('image_task', task['id']).get('local_stop_requested'))
        self.assertFalse(self.h.listing('generation_stop'))

    def test_finished_task_is_preserved_and_historical_id_fallback_works(self):
        context = self.conversation(self.a, 'image', 'legacy')
        task = self.submit(self.a, 'image', context, 'legacy')
        old = self.h.get('image_task', task['id'])
        old.pop('request_id')
        self.h.save('image_task', old)
        self.stop('image', request_id='legacy')
        self.assertEqual(self.h.get('image_task', task['id'])['status'], 'cancelled')
        finished = self.submit(self.a, 'image', context, 'completed')
        self.finish('image', finished)
        before = self.h.get('image_task', finished['id'])
        result = self.stop('image', task_id=finished['id'])
        self.assertEqual(result['status'], 'already_finished')
        self.assertEqual(self.h.get('image_task', finished['id']), before)

    def test_stop_while_submission_receipt_is_delayed_cancels_committed_queue(self):
        context = self.conversation(self.a, 'image', 'race')
        entered, release, stopping = threading.Event(), threading.Event(), threading.Event()
        original = self.h._create_image_generation
        def delayed(request):
            receipt = original(request)
            entered.set()
            self.assertTrue(release.wait(10))
            return receipt
        def stop():
            stopping.set()
            return self.stop('image', request_id='race-task')
        with patch.object(self.h, '_create_image_generation', side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            submission = pool.submit(self.submit, self.a, 'image', context, 'race-task')
            self.assertTrue(entered.wait(10))
            stopped = pool.submit(stop)
            self.assertTrue(stopping.wait(10))
            release.set()
            task = submission.result(15)
            self.assertEqual(stopped.result(15)['status'], 'stopped')
        self.assertEqual(self.h.get('image_task', task['id'])['status'], 'cancelled')
        self.assertFalse(self.state(self.a, context)['is_busy'])

    def test_agent_precreate_and_followup_tombstones(self):
        service = self.h.app.state.agent_service
        with patch.object(service, 'kick'):
            self.stop('agent', request_id='not-created')
            rejected = self.request(self.a, 'POST', '/api/agent/runs', {'text': '你好', 'request_id': 'not-created'}, 409)
            self.assertEqual(rejected['detail']['code'], 'generation_request_stopped')
            run = self.request(self.a, 'POST', '/api/agent/runs', {'text': '你好', 'request_id': 'new-agent'}, 202)['run']
            self.stop('agent', request_id='not-arrived', run_id=run['id'])
            rejected = self.request(self.a, 'POST', '/api/agent/runs/' + run['id'] + '/messages',
                {'text': '新消息', 'request_id': 'not-arrived'}, 409)
            self.assertEqual(rejected['detail']['code'], 'generation_request_stopped')
            self.assertEqual(service.get(run['id'])['status'], 'running')


del Fixture

if __name__ == '__main__':
    unittest.main()
