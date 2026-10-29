"""Stopped media keep late receipts without resuming generation or canvas binding."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from studio_graph import binding_signature
from test_generation_stop import GenerationStopTests as Fixture


class MediaStopResultsTests(unittest.TestCase):
    setUpClass = Fixture.__dict__['setUpClass']
    tearDownClass = Fixture.__dict__['tearDownClass']
    for name in ('login', 'request', 'upload', 'owned_save', 'fixture_setup', 'setUp', 'tearDown',
                 'conversation', 'payload', 'route', 'submit', 'finish', 'state', 'stop'):
        locals()[name] = getattr(Fixture, name)

    def test_timeout_is_terminal_against_late_worker_writes_and_resume(self):
        for kind, store in (('image', 'image_task'), ('video', 'task')):
            context = self.conversation(self.a, kind, 'timeout-' + kind)
            submitted = self.submit(self.a, kind, context, 'timeout-' + kind)
            before = self.h.get(store, submitted['id'])
            job = self.h.generation_queue.job(store, before['id'])
            self.h.generation_queue.expire(job, before, 100)
            for status in ('running', 'blocked', 'done' if kind == 'video' else 'succeeded'):
                saved = self.h.save(store, {**before, 'status': status, 'submit_unknown': True,
                    'polling_interrupted': True, 'provider_task_id': 'late-upstream'})
                self.assertEqual(saved['status'], 'failed')
                self.assertEqual(saved['queue_timed_out_at'], 100)
                self.assertFalse(saved['submit_unknown'])
            if kind == 'video':
                with patch.object(self.h, 'start_generation_worker') as worker:
                    self.assertEqual(self.h.reconcile_video_task(before['id'])['status'], 'failed')
                    worker.assert_not_called()

    def test_success_winning_timeout_race_is_preserved(self):
        context = self.conversation(self.a, 'image', 'timeout-race')
        submitted = self.submit(self.a, 'image', context, 'timeout-race')
        before = self.h.get('image_task', submitted['id'])
        job = self.h.generation_queue.job('image_task', before['id'])
        self.h.save('image_task', {**before, 'status': 'succeeded'})
        self.h.generation_queue.expire(job, before, 100)
        self.assertEqual(self.h.get('image_task', before['id'])['status'], 'succeeded')
        self.assertEqual(self.h.generation_queue.job('image_task', before['id'])['state'], 'done')

    def test_late_provider_snapshots_keep_stop_markers_and_receipts(self):
        for kind, store in (('image', 'image_task'), ('video', 'task'), ('audio', 'audio_task')):
            with self.subTest(kind=kind):
                context = self.conversation(self.a, kind, 'late-' + kind)
                submitted = self.submit(self.a, kind, context, 'late-' + kind)
                old = self.h.get(store, submitted['id'])
                old = self.h.save(store, {**old, 'status': 'running'})
                if store != 'audio_task':
                    self.h.generation_queue.patch(self.h.generation_queue.job(store, old['id']), state='provider')
                self.stop(kind, task_id=old['id'])
                stopped = self.h.get(store, old['id'])

                # The worker captured this object before Stop. A finished receipt
                # is valid, but overwriting it must not restore the active request.
                saved = self.h.save(store, {**old, 'status': 'done' if kind == 'video' else 'succeeded',
                    'provider_receipt': 'late-receipt-' + kind})
                for key in ('stop_requested', 'local_stop_requested', 'stopped_at', 'provider_may_continue'):
                    self.assertEqual(saved[key], stopped[key], key)
                self.assertTrue(saved['local_stop_requested'])
                self.assertEqual(saved['provider_receipt'], 'late-receipt-' + kind)
                self.assertEqual(self.h.get(store, old['id']), saved)
                self.assertFalse(self.state(self.a, context)['is_busy'])

    def test_late_success_remains_in_history_without_rebinding_stopped_nodes(self):
        for kind, store, node_type in (('image', 'image_task', 'asset'), ('video', 'task', 'video')):
            with self.subTest(kind=kind):
                node = {'id': 'stopped-' + kind, 'type': node_type, 'title': '已停止的节点',
                    'mujian': {'role': 'asset' if kind == 'image' else 'video', 'inputs': []}}
                graph = self.request(self.a, 'POST', '/api/projects',
                    {'name': '停止结果测试-' + kind, 'nodes': [node], 'edges': []}, 201)
                old = self.owned_save('dingyufu', store, {'id': 'late-node-' + kind,
                    'project_id': graph['id'], 'node_id': node['id'], 'status': 'running',
                    'request_id': 'late-node-' + kind, 'created_at': 1,
                    'params': {'node_id': node['id'], 'prompt': '测试', 'duration': 5},
                    'studio_binding': binding_signature(graph, node)})
                self.stop(kind, task_id=old['id'])
                if kind == 'image':
                    asset = self.owned_save('dingyufu', 'asset', {**self.aa, 'id': 'late-image-result',
                        'project_id': graph['id'], 'source': 'generated', 'task_id': old['id']})
                    receipt = {**old, 'status': 'succeeded', 'asset': asset, 'assets': [asset]}
                else:
                    # Only file persistence is under test; no decoding or provider
                    # request is needed for this already-cached receipt.
                    (self.h.UPLOADS / 'stopped-video.mp4').write_bytes(b'isolated-video-fixture')
                    receipt = {**old, 'status': 'done', 'video_url': '/uploads/stopped-video.mp4'}
                self.h.save(store, receipt)
                if kind == 'video':
                    asset = self.request(self.a, 'POST', '/api/tasks/' + old['id'] + '/history-asset', {})

                reopened = self.request(self.a, 'GET', '/api/projects/' + graph['id'])
                self.assertEqual(reopened['revision'], graph['revision'])
                self.assertFalse(reopened['nodes'][0].get('assetId'))
                self.assertFalse(reopened['nodes'][0].get('image_task_id'))
                self.assertFalse(reopened['nodes'][0].get('referenceTaskId'))
                history = self.request(self.a, 'GET', '/api/assets?collection=history')
                self.assertIn(asset['id'], {item['id'] for item in history})
                self.assertEqual(self.a.request('GET', asset['url'])[0], 200)

                # Positive control: the same valid binding still recovers an
                # ordinary, newer successful task after a browser reload.
                self.owned_save('dingyufu', store, {**receipt, 'id': 'ordinary-node-' + kind,
                    'request_id': 'ordinary-node-' + kind, 'created_at': 2})
                recovered = self.request(self.a, 'GET', '/api/projects/' + graph['id'])
                self.assertTrue(recovered['nodes'][0].get('assetId'))
                self.assertEqual(recovered['nodes'][0].get('image_task_id' if kind == 'image' else 'referenceTaskId'),
                    'ordinary-node-' + kind)

    def test_stop_during_first_image_keeps_receipt_and_prevents_second_submission(self):
        context = self.conversation(self.a, 'image', 'batch-stop')
        task = self.request(self.a, 'POST', '/api/images/generations',
            {**self.payload('image', context, 'batch-stop'), 'count': 3}, 202)
        self.h.generation_queue.patch(self.h.generation_queue.job('image_task', task['id']), state='provider')
        entered, release = threading.Event(), threading.Event()

        def provider(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError('test did not release first image response')
            return {'data': [{'url': 'https://fixture.invalid/first.png'}]}

        with patch.object(self.h, 'provider_json', side_effect=provider) as upstream, \
                patch.object(self.h, 'download_generated_image', return_value=(self.image, '.png')), \
                ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(self.h.run_image_generation, task['id'], self.h.internal_image_config())
            try:
                self.assertTrue(entered.wait(10))
                stopped = self.stop('image', task_id=task['id'])
                self.assertTrue(stopped['provider_may_continue'])
            finally:
                release.set()
            worker.result(15)

        saved = self.h.get('image_task', task['id'])
        self.assertEqual(upstream.call_count, 1)
        self.assertEqual(saved['status'], 'cancelled')
        self.assertTrue(saved['local_stop_requested'])
        self.assertEqual(len(saved['assets']), 1)
        self.assertEqual(saved['asset']['id'], saved['assets'][0]['id'])
        self.assertNotIn(task['id'], self.h.ACTIVE_IMAGE_TASKS)
        self.assertFalse(self.state(self.a, context)['is_busy'])
        self.assertIn(saved['asset']['id'], {asset['id'] for asset in self.request(self.a, 'GET', '/api/assets')})
        self.assertEqual(self.a.request('GET', saved['asset']['url'])[0], 200)


del Fixture

if __name__ == '__main__':
    unittest.main()
