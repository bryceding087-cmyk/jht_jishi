"""Real ASGI submission races without provider calls or production data."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event, select
from storage.schema import records
from test_user_isolation import UserIsolationTests as _Fixtures


class ConversationGenerationTests(unittest.TestCase):
    setUpClass = _Fixtures.__dict__['setUpClass']
    tearDownClass = _Fixtures.__dict__['tearDownClass']
    login = _Fixtures.login
    request = _Fixtures.request
    upload = _Fixtures.upload
    owned_save = _Fixtures.owned_save
    fixture_setup = _Fixtures.setUp

    def setUp(self):
        self.fixture_setup()
        self.h.ACTIVE_VIDEO_TASKS.clear()
        for kind, mid in [('image', self.h.DEFAULT_IMAGE_MODEL_ID), ('video', self.h.DEFAULT_MODEL_ID), ('audio', self.h.DEFAULT_AUDIO_MODEL['id'])]:
            self.h.update_registry_model(kind, mid, self.h.RegistryModelUpdate(api_key='fixture-key'))
        self.workers = []
        real_thread = threading.Thread
        def thread_factory(*args, **kwargs):
            target = kwargs.get('target')
            if target not in (self.h.run_image_generation, self.h.run_audio_generation, self.h.run_provider_task):
                return real_thread(*args, **kwargs)
            def start():
                tid = kwargs['args'][0]
                # A fresh connection must see the queued record before any
                # provider worker starts; no open submission transaction.
                with self.h.database.engine.connect() as db:
                    value = db.execute(select(records.c.body).where(records.c.id == tid)).scalar_one()
                kind = 'image_task' if target == self.h.run_image_generation else 'audio_task' if target == self.h.run_audio_generation else 'task'
                self.assertTrue(self.h.manual_generation.live(kind, value))
                self.workers.append((kind, kwargs['args']))
            return SimpleNamespace(start=start)
        self.thread_patch = patch.object(self.h.threading, 'Thread', side_effect=thread_factory)
        self.thread_patch.start()

    def tearDown(self):
        self.thread_patch.stop()

    def conversation(self, browser, kind, key):
        return self.request(browser, 'POST', '/api/conversations',
            {'title': key, 'kind': kind, 'prompt': '测试', 'settings': {}, 'request_id': key}, 201)

    def payload(self, kind, context, key):
        body = {'project_id': context['id'], 'prompt': '测试', 'request_id': key}
        if kind == 'video':
            body.update(node_id='conversation-video', model_config_id=self.h.DEFAULT_MODEL_ID, resolution='768p')
        return body

    def route(self, kind):
        return {'image': '/api/images/generations', 'audio': '/api/audio/generations', 'video': '/api/tasks'}[kind]

    def submit(self, browser, kind, context, key, status=202):
        return self.request(browser, 'POST', self.route(kind), self.payload(kind, context, key), status)

    def finish(self, kind, task, status='succeeded'):
        store = {'image': 'image_task', 'audio': 'audio_task', 'video': 'task'}[kind]
        value = self.h.get(store, task['id'])
        self.h.save(store, {**value, 'status': status})
        getattr(self.h, {'image': 'ACTIVE_IMAGE_TASKS', 'audio': 'ACTIVE_AUDIO_TASKS', 'video': 'ACTIVE_VIDEO_TASKS'}[kind]).discard(task['id'])

    def state(self, browser, context=None, status=200):
        query = '?conversation_id=' + context['id'] if context else ''
        return self.request(browser, 'GET', '/api/conversations/generation-state' + query, expected=status)

    def test_busy_blocks_only_same_conversation_and_repeat_is_idempotent(self):
        contexts = {kind: self.conversation(self.a, kind, kind) for kind in ('image', 'audio', 'video')}
        task = self.submit(self.a, 'image', contexts['image'], 'first')
        state = self.state(self.a, contexts['image'])
        self.assertEqual(state, {'is_busy': True, 'conversation_id': contexts['image']['id'],
            'task': {'id': task['id'], 'kind': 'image', 'status': 'queued'}})
        self.assertFalse(self.state(self.a)['is_busy'], 'home has no existing conversation to block')
        self.assertEqual(self.submit(self.a, 'image', contexts['image'], 'first')['id'], task['id'])
        for kind in contexts:
            blocked = self.submit(self.a, kind, contexts['image'], 'same-' + kind, 409)
            self.assertEqual(blocked['detail']['code'], 'conversation_generation_busy')
        for kind in ('audio', 'video'):
            self.submit(self.a, kind, contexts[kind], 'other-' + kind)
        another = self.conversation(self.a, 'image', 'new')
        self.submit(self.a, 'image', another, 'new')
        self.assertEqual(self.conversation(self.a, 'image', 'image')['id'], contexts['image']['id'])
        self.assertEqual(len(self.workers), 4)

    def test_conversation_list_discovers_persisted_media_tasks_without_submitting(self):
        contexts = {kind: self.conversation(self.a, kind, 'return-' + kind) for kind in ('image', 'video', 'audio')}
        tasks = {kind: self.submit(self.a, kind, context, 'return-' + kind) for kind, context in contexts.items()}
        foreign = self.conversation(self.b, 'image', 'foreign-return')
        self.submit(self.b, 'image', foreign, 'foreign-return')
        self.submit(self.a, 'image', self.pa, 'canvas-return')
        count = len(self.workers)
        listing = self.request(self.a, 'GET', '/api/conversations')
        self.assertEqual({item['id'] for item in listing}, {c['id'] for c in contexts.values()})
        for item in listing:
            self.assertEqual(item['active_task'], {'id': tasks[item['kind']]['id'], 'kind': item['kind'], 'status': 'queued'})
            self.assertEqual(item['status'], 'queued')
            self.assertNotIn('api_key', json.dumps(item))
            detail = self.request(self.a, 'GET', '/api/conversations/' + item['id'])
            self.assertEqual(detail['tasks'][0]['id'], tasks[item['kind']]['id'])
        self.assertEqual(len(self.workers), count, 'listing/reopening live jobs cannot create another worker')
        self.finish('image', tasks['image'])
        updated = {c['id']: c for c in self.request(self.a, 'GET', '/api/conversations')}
        self.assertIsNone(updated[contexts['image']['id']]['active_task'])
        self.assertEqual(updated[contexts['image']['id']]['status'], 'succeeded')
        self.request(self.a, 'DELETE', '/api/recent-items/conversation/' + contexts['audio']['id'], expected=204)
        self.assertNotIn(contexts['audio']['id'], {c['id'] for c in self.request(self.a, 'GET', '/api/conversations')})

    def test_interrupted_video_is_discoverable_even_after_a_later_failed_submission(self):
        context = self.conversation(self.a, 'video', 'return-interrupted')
        task = self.submit(self.a, 'video', context, 'return-interrupted')
        self.h.update_task(task['id'], status='blocked', provider_task_id='existing-id', polling_interrupted=True)
        self.h.save('task', {**self.h.get('task', task['id']), 'id': 'later-failure', 'status': 'failed',
            'polling_interrupted': False, 'provider_task_id': None, 'created_at': 9999999999})
        item = self.request(self.a, 'GET', '/api/conversations')[0]
        self.assertEqual(item['active_task']['id'], task['id'])
        self.assertEqual(item['status'], 'running')
        self.h.update_task(task['id'], deleted_at=1)
        self.assertIsNone(self.request(self.a, 'GET', '/api/conversations')[0]['active_task'])

    def test_reopening_images_and_audio_recovers_interrupted_status_without_resubmission(self):
        for kind in ('image', 'audio'):
            context = self.conversation(self.a, kind, 'interrupted-' + kind)
            task = self.submit(self.a, kind, context, 'interrupted-' + kind)
            getattr(self.h, 'ACTIVE_' + kind.upper() + '_TASKS').clear()
            count = len(self.workers)
            detail = self.request(self.a, 'GET', '/api/conversations/' + context['id'])
            self.assertEqual(detail['tasks'][0]['id'], task['id'])
            self.assertEqual(detail['tasks'][0]['status'], 'failed')
            self.assertEqual(len(self.workers), count)

    def test_simultaneous_different_conversations_start_independent_tasks(self):
        for kinds in [('image', 'audio'), ('image', 'image')]:
            with self.subTest(kinds=kinds):
                contexts = [self.conversation(self.a, kind, f'parallel-{kinds}-{i}') for i, kind in enumerate(kinds)]
                barrier = threading.Barrier(2)
                before = len(self.workers)
                def submit(index):
                    barrier.wait()
                    kind = kinds[index]
                    return self.a.request('POST', self.route(kind), self.payload(kind, contexts[index], f'parallel-{kinds}-{index}'))[0]
                with ThreadPoolExecutor(max_workers=2) as pool:
                    self.assertEqual(sorted(pool.map(submit, range(2))), [202, 202])
                self.assertEqual(len(self.workers), before + 2)
                for context in contexts:
                    self.assertTrue(self.state(self.a, context)['is_busy'])

    def test_simultaneous_different_requests_in_same_conversation_start_one_task(self):
        context = self.conversation(self.a, 'image', 'same-context')
        barrier = threading.Barrier(2)
        def submit(index):
            barrier.wait()
            return self.a.request('POST', self.route('image'), self.payload('image', context, f'request-{index}'))[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit, range(2))), [202, 409])
        self.assertEqual(len(self.workers), 1)

    def test_scoped_state_rejects_foreign_missing_and_deleted_conversations(self):
        context = self.conversation(self.a, 'image', 'private')
        self.submit(self.a, 'image', context, 'private')
        self.state(self.b, context, 404)
        self.submit(self.b, 'image', context, 'foreign', 404)
        self.state(self.a, {'id': 'missing'}, 404)
        self.request(self.a, 'DELETE', '/api/recent-items/conversation/' + context['id'], expected=204)
        self.state(self.a, context, 404)
        self.state(self.admin, context, 404)
        self.assertFalse(self.state(self.a)['is_busy'])

    def test_simultaneous_identical_requests_reuse_the_same_task(self):
        context = self.conversation(self.a, 'image', 'same')
        barrier = threading.Barrier(2)
        def submit(_):
            barrier.wait()
            return self.a.request('POST', self.route('image'), self.payload('image', context, 'same'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, range(2)))
        self.assertEqual([value[0] for value in responses], [202, 202])
        self.assertEqual(len({json.loads(value[2])['id'] for value in responses}), 1)
        self.assertEqual(len(self.workers), 1)

    def test_running_video_allows_other_image_video_and_audio_conversations(self):
        context = self.conversation(self.a, 'video', 'busy-video')
        running = self.submit(self.a, 'video', context, 'busy-video')
        self.h.update_task(running['id'], status='running', provider_task_id='existing-video-job')
        self.assertTrue(self.state(self.a, context)['is_busy'])
        self.submit(self.a, 'video', context, 'same-video', 409)
        for kind in ('image', 'video', 'audio'):
            other = self.conversation(self.a, kind, 'independent-' + kind)
            self.assertFalse(self.state(self.a, other)['is_busy'])
            self.submit(self.a, kind, other, 'independent-' + kind)
        self.assertEqual(len(self.workers), 4)
        self.assertEqual(self.h.get('task', running['id'])['provider_task_id'], 'existing-video-job')

    def test_success_failure_and_new_context_allow_next_submission(self):
        context = self.conversation(self.a, 'image', 'first')
        first = self.submit(self.a, 'image', context, 'first')
        self.finish('image', first)
        self.assertFalse(self.state(self.a, context)['is_busy'])
        self.submit(self.a, 'image', context, 'image-after-success')
        audio = self.conversation(self.a, 'audio', 'audio-after-success')
        second = self.submit(self.a, 'audio', audio, 'audio-after-success')
        self.finish('audio', second, 'failed')
        self.assertFalse(self.state(self.a, audio)['is_busy'])
        self.submit(self.a, 'audio', audio, 'audio-after-failure')
        video = self.conversation(self.a, 'video', 'video-after-failure')
        third = self.submit(self.a, 'video', video, 'video-after-failure')
        self.finish('video', third, 'done')
        self.assertFalse(self.state(self.a, video)['is_busy'])
        self.submit(self.a, 'video', video, 'video-after-done')

    def test_accounts_are_independent_including_admin(self):
        contexts = [(browser, self.conversation(browser, 'image', key)) for browser, key in
                    [(self.a, 'member-a'), (self.b, 'member-b'), (self.admin, 'admin')]]
        for index, (browser, context) in enumerate(contexts):
            self.assertFalse(self.state(browser, context)['is_busy'])
            task = self.submit(browser, 'image', context, 'account-' + str(index))
            self.assertEqual(self.state(browser, context)['task']['id'], task['id'])
        self.assertEqual(len(self.workers), 3)

    def test_admin_and_owner_share_same_conversation_gate_only(self):
        context = self.conversation(self.a, 'image', 'member-context')
        task = self.submit(self.admin, 'image', context, 'admin-submission')
        self.assertEqual(self.state(self.admin, context)['task']['id'], task['id'])
        self.assertEqual(self.state(self.a, context)['task']['id'], task['id'])
        self.submit(self.a, 'image', context, 'member-same-context', 409)
        another = self.conversation(self.a, 'audio', 'member-still-free')
        self.submit(self.a, 'audio', another, 'member-submission')

    def test_commit_failure_rolls_back_task_and_active_registration(self):
        context = self.conversation(self.a, 'image', 'commit-failure')
        original = self.h.record_store.upsert
        def mark_commit(connection, kind, value, **kwargs):
            result = original(connection, kind, value, **kwargs)
            if kind == 'image_task' and value.get('status') == 'queued':
                connection.info['fail_generation_commit'] = True
            return result
        def fail_commit(connection):
            if connection.info.pop('fail_generation_commit', False):
                raise RuntimeError('forced generation commit failure')
        event.listen(self.h.database.engine, 'commit', fail_commit)
        try:
            with patch.object(self.h.record_store, 'upsert', side_effect=mark_commit):
                with self.assertRaisesRegex(RuntimeError, 'forced generation commit failure'):
                    self.a.request('POST', self.route('image'), self.payload('image', context, 'failed-commit'))
        finally:
            event.remove(self.h.database.engine, 'commit', fail_commit)
        self.assertFalse(self.h.ACTIVE_IMAGE_TASKS)
        self.assertFalse(self.workers)
        self.assertFalse(self.h.listing('image_task'))
        self.assertFalse(self.state(self.a, context)['is_busy'])
        self.submit(self.a, 'image', context, 'after-rollback')

    def test_canvas_and_agent_generation_do_not_use_manual_conversation_gate(self):
        context = self.conversation(self.a, 'image', 'manual')
        first = self.submit(self.a, 'image', context, 'manual')
        canvas = self.submit(self.a, 'image', self.pa, 'canvas')
        agent = self.owned_save('dingyufu', 'conversation', {'id': 'agent-context', 'kind': 'agent', 'nodes': [], 'name': 'agent'})
        automatic = self.submit(self.a, 'image', agent, 'automatic')
        self.assertEqual(len(self.workers), 3)
        self.finish('image', first)
        self.assertFalse(self.state(self.a, context)['is_busy'])
        self.assertEqual(self.h.get('image_task', canvas['id'])['status'], 'queued')
        self.assertEqual(self.h.get('image_task', automatic['id'])['status'], 'queued')

    def test_read_only_state_and_submission_recover_interrupted_images(self):
        context = self.conversation(self.a, 'image', 'restart')
        first = self.submit(self.a, 'image', context, 'before-restart')
        self.h.ACTIVE_IMAGE_TASKS.clear()
        self.assertFalse(self.state(self.a, context)['is_busy'])
        self.assertEqual(self.h.get('image_task', first['id'])['status'], 'queued')
        second = self.submit(self.a, 'image', context, 'after-restart')
        self.assertEqual(self.h.get('image_task', first['id'])['status'], 'failed')
        self.assertEqual(self.state(self.a, context)['task']['id'], second['id'])

    def test_interrupted_video_resumes_existing_query_and_still_blocks(self):
        context = self.conversation(self.a, 'video', 'video-restart')
        first = self.submit(self.a, 'video', context, 'video-before')
        self.h.update_task(first['id'], provider_task_id='existing-provider-job')
        self.h.ACTIVE_VIDEO_TASKS.clear()
        self.submit(self.a, 'video', context, 'video-after', 409)
        self.assertEqual(len(self.workers), 2)
        self.assertTrue(self.workers[-1][1][-1])
        self.assertEqual(self.state(self.a, context)['task']['id'], first['id'])
        self.finish('video', first, 'done')
        self.submit(self.a, 'video', context, 'video-after')

    def test_video_without_provider_id_and_worker_start_failure_release_gate(self):
        context = self.conversation(self.a, 'video', 'missing-provider')
        first = self.submit(self.a, 'video', context, 'before')
        self.h.ACTIVE_VIDEO_TASKS.clear()
        second = self.submit(self.a, 'video', context, 'after')
        self.assertEqual(self.h.get('task', first['id'])['status'], 'blocked')
        self.finish('video', second, 'failed')
        image = self.conversation(self.a, 'image', 'failed-start')
        with patch.object(self.h.threading, 'Thread', return_value=SimpleNamespace(start=lambda: (_ for _ in ()).throw(RuntimeError('start failed')))):
            self.submit(self.a, 'image', image, 'failed-start', 503)
        self.assertFalse(self.state(self.a, image)['is_busy'])
        self.submit(self.a, 'image', image, 'retry-start')


del _Fixtures
