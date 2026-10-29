import copy
import threading
import time
import unittest
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from data_access import access_scope, current_principal, require_access
from generation_queue import GenerationQueue, request_result_unknown
from generation_stop import GenerationStop


class Host:
    def __init__(self):
        self.records = {}
        self.ACTIVE_IMAGE_TASKS = set()
        self.ACTIVE_VIDEO_TASKS = set()
        self.configs = {'shared': {'base_url': 'https://fixture.invalid', 'api_key': 'test-key'},
                        'other': {'base_url': 'https://fixture.invalid', 'api_key': 'other-key'}}
        self.calls = []
        self.unavailable = set()
        self.generation_stop = GenerationStop(self)

    def save(self, kind, body):
        self.records[kind, body['id']] = copy.deepcopy(body)
        return copy.deepcopy(body)

    def raw_record(self, kind, tid):
        body = copy.deepcopy(self.records.get((kind, tid)))
        if body: require_access(kind, body)
        return body

    def get(self, kind, tid):
        body = self.raw_record(kind, tid)
        if not body: raise HTTPException(404, 'missing')
        return body

    def listing(self, kind):
        return [copy.deepcopy(v) for (k,_),v in self.records.items() if k == kind]

    @contextmanager
    def worker_scope(self, task):
        with access_scope({'id': task['owner_id'], 'role': 'user'}): yield

    def internal_registry_model(self, kind, mid):
        if str(current_principal()['id']) in self.unavailable: raise HTTPException(428, 'expired')
        return copy.deepcopy(self.configs[mid or 'shared'])

    def resolve_model_config(self, mid, model):
        return self.internal_registry_model('video', mid)

    def model_for_generation(self, config): return config
    def refresh_market_credentials(self, task): return task

    def run_image_generation(self, tid, config):
        self.calls.append((tid, str(current_principal()['id']), config['api_key']))
        task = self.get('image_task', tid)
        self.save('image_task', {**task, 'status': 'succeeded'})

    def run_provider_task(self, tid, params, config):
        self.calls.append((tid, str(current_principal()['id']), config['api_key']))
        task = self.get('task', tid)
        self.save('task', {**task, 'status': 'running', 'provider_task_id': 'upstream-' + tid})


class GenerationQueueTests(unittest.TestCase):
    def test_lost_responses_hold_slots_but_definite_connect_failures_do_not(self):
        import socket
        from urllib.error import URLError
        for error,unknown in [(TimeoutError(),True),(URLError(ConnectionResetError()),True),
                              (URLError(socket.gaierror()),False),(URLError(ConnectionRefusedError()),False),
                              (RuntimeError('模型已拒绝请求'),False)]:
            self.assertEqual(request_result_unknown(error),unknown)
        wrapped=RuntimeError('连接中断');wrapped.__cause__=URLError(ConnectionResetError())
        self.assertTrue(request_result_unknown(wrapped))

    def setUp(self):
        self.host = Host()
        self.queue = GenerationQueue(self.host)
        self.host.generation_queue = self.queue
        self.admitted = []
        self.thread = patch('generation_queue.threading.Thread', side_effect=lambda **kw:
                            SimpleNamespace(start=lambda: self.admitted.append(kw['args'][0])))
        self.thread.start(); self.addCleanup(self.thread.stop)

    def enqueue(self, kind='image_task', user='a', model='shared'):
        tid = kind + '-' + str(len(self.host.records))
        task = self.host.save(kind, {'id': tid, 'owner_id': user, 'creator_id': user, 'status': 'queued',
            'model_config_id': model, 'params': {'model_config_id': model}, 'created_at': time.time()})
        self.queue.active_set(kind).add(tid)
        self.queue.enqueue(kind, task, self.host.configs[model])
        return tid

    def test_user_limits_fifo_and_completion_refill(self):
        images = [self.enqueue() for _ in range(8)]
        videos = [self.enqueue('task') for _ in range(12)]
        self.queue.tick()
        self.assertEqual([j['task_id'] for j in self.admitted], images + videos[:10])
        job = self.admitted[len(images)]
        self.queue.execute(job)
        task = self.host.get('task', videos[0])
        self.host.save('task', {**task, 'status': 'done'})
        self.queue.tick()
        self.assertEqual(self.admitted[-1]['task_id'], videos[10])
        self.assertIsNone(self.queue.public('image_task', self.host.get('image_task', images[4]))['queue_limit'])
        info = self.queue.public('task', self.host.get('task', videos[-1]))
        self.assertEqual(info['queue_limit'], 10)
        self.assertEqual(info['queue_global_limit'], 100)

    def test_stop_before_worker_submission_does_not_call_provider(self):
        tid = self.enqueue()
        self.queue.tick()
        self.host.generation_stop.stop_task('image_task', tid)
        self.queue.execute(self.admitted[0])
        self.assertFalse(self.host.calls)
        self.assertEqual(self.host.get('image_task', tid)['status'], 'cancelled')
        self.assertFalse(self.host.get('image_task', tid)['provider_may_continue'])
        self.assertEqual(self.queue.job('image_task', tid)['state'], 'cancelled')

    def test_request_tombstone_prevents_late_enqueue(self):
        self.host.generation_stop.remember('image', 'stopped-request', 'a')
        task = self.host.save('image_task', {'id': 'late', 'owner_id': 'a', 'creator_id': 'a',
            'status': 'queued', 'request_id': 'stopped-request'})
        self.queue.enqueue('image_task', task, self.host.configs['shared'])
        self.queue.tick()
        self.assertFalse(self.admitted)
        self.assertEqual(self.host.get('image_task', 'late')['status'], 'cancelled')

    def test_shared_credentials_allow_ten_per_account_with_fair_scheduling(self):
        for user in ('a', 'b'):
            for _ in range(12): self.enqueue('task', user=user)
        self.queue.tick()
        self.assertEqual(len(self.admitted), 20)
        self.assertEqual([j['user_id'] for j in self.admitted], ['a', 'b'] * 10)
        for _ in range(4): self.enqueue('task', user='c', model='other')
        self.queue.tick()
        self.assertEqual(sum(j['user_id'] == 'c' for j in self.admitted), 4)

    def test_account_limit_spans_models_and_projects(self):
        for index in range(12):
            tid = self.enqueue('task', model='shared' if index % 2 else 'other')
            task = self.host.get('task', tid)
            self.host.save('task', {**task, 'project_id': 'project-' + str(index)})
        self.queue.tick()
        self.assertEqual(len(self.admitted), 10)
        self.assertEqual(sum(j['state'] == 'waiting' for j in self.queue.jobs()), 2)

    def test_system_limit_spans_accounts_and_credentials_and_refills_one_slot(self):
        for user in range(11):
            for _ in range(10):
                self.enqueue('task', user=str(user), model='shared' if user % 2 else 'other')
        self.queue.tick()
        self.assertEqual(len(self.admitted), 100)
        self.assertLessEqual(max(Counter(j['user_id'] for j in self.admitted).values()), 10)
        self.assertEqual(sum(j['state'] == 'waiting' for j in self.queue.jobs()), 10)
        self.queue.tick()
        self.assertEqual(len(self.admitted), 100)
        # A full video queue does not consume image capacity.
        images = [self.enqueue() for _ in range(70)]
        self.queue.tick()
        self.assertEqual([j['task_id'] for j in self.admitted if j['kind'] == 'image_task'], images)
        first = self.host.get('task', self.admitted[0]['task_id'])
        self.host.save('task', {**first, 'status': 'done'})
        self.queue.tick()
        self.assertEqual(sum(j['kind'] == 'task' for j in self.admitted), 101)
        self.assertEqual(sum(j['kind'] == 'task' and j['state'] == 'executing' for j in self.queue.jobs()), 100)

    def test_restart_counts_provider_and_unknown_jobs_in_system_capacity(self):
        for user in range(10):
            for index in range(10):
                tid = self.enqueue('task', user=str(user))
                task = self.host.get('task', tid)
                if index % 2:
                    self.host.save('task', {**task, 'provider_task_id': 'upstream-' + tid})
                # Old persisted limits and unidentified keys must not bypass or
                # further reduce the new system/account capacity.
                self.queue.patch(self.queue.job('task', tid), state='executing', pool='legacy:old', pool_limit=5)
        waiting = self.enqueue('task', user='new-user', model='other')
        recovered = GenerationQueue(self.host)
        recovered.recover()
        recovered.tick()
        self.assertEqual(self.admitted, [])
        self.assertEqual(sum(j['state'] == 'provider' for j in recovered.jobs()), 50)
        self.assertEqual(sum(j['state'] == 'unknown' for j in recovered.jobs()), 50)
        unknown = next(j for j in recovered.jobs() if j['state'] == 'unknown')
        with access_scope({'id': unknown['user_id'], 'role': 'user'}):
            recovered.resolve('task', unknown['task_id'])
        recovered.tick()
        self.assertEqual([j['task_id'] for j in self.admitted], [waiting])
        self.assertEqual(self.host.calls, [])

    def test_video_slot_remains_occupied_after_submission_and_query_interruption(self):
        tids = [self.enqueue('task') for _ in range(11)]
        self.queue.tick()
        self.queue.execute(self.admitted[0]); self.queue.tick()
        self.assertEqual(self.queue.job('task', tids[0])['state'], 'provider')
        self.assertEqual(len(self.admitted), 10)
        task = self.host.get('task', tids[0]); self.host.save('task', {**task, 'status': 'done'})
        self.queue.tick(); self.assertEqual(self.admitted[-1]['task_id'], tids[10])

    def test_restart_requeues_only_unsent_work_and_reserves_unknown_slots(self):
        tids = [self.enqueue('task') for _ in range(11)]
        self.queue.tick()
        recovered = GenerationQueue(self.host); recovered.recover()
        for tid in tids[:10]:
            self.assertEqual(recovered.job('task', tid)['state'], 'unknown')
            self.assertTrue(self.host.get('task', tid)['submit_unknown'])
        self.admitted.clear(); recovered.tick()
        self.assertEqual(self.admitted, [])
        with access_scope({'id': 'a', 'role': 'user'}): recovered.resolve('task', tids[0])
        recovered.tick()
        self.assertEqual(self.admitted[0]['task_id'], tids[10])
        self.assertEqual(self.host.calls, [])

    def test_cancel_is_isolated_idempotent_and_never_starts_paid_work(self):
        tid = self.enqueue()
        with access_scope({'id': 'b', 'role': 'user'}), self.assertRaises(HTTPException):
            self.queue.cancel('image_task', tid)
        with access_scope({'id': 'a', 'role': 'user'}):
            self.queue.cancel('image_task', tid); self.queue.cancel('image_task', tid)
        self.queue.tick(); self.assertEqual(self.admitted, [])
        self.assertNotIn(tid, self.host.ACTIVE_IMAGE_TASKS)
        started = self.enqueue(); self.queue.tick()
        with self.assertRaises(HTTPException): self.queue.cancel('image_task', started)

    def test_expired_credentials_do_not_block_other_users_or_submit_requests(self):
        a = self.enqueue(user='a'); b = self.enqueue(user='b')
        self.host.unavailable.add('a'); self.queue.tick()
        self.assertEqual(self.queue.job('image_task', a)['state'], 'credentials')
        self.assertEqual([j['task_id'] for j in self.admitted], [b])
        self.host.unavailable.clear()
        self.queue.refresh_credentials('image_task', self.host.get('image_task', a))
        self.queue.tick(); self.assertEqual(self.admitted[-1]['task_id'], a)
        self.assertNotIn('test-key', str(self.host.records))

    def test_changed_credentials_are_rechecked_before_claim(self):
        for _ in range(4): self.enqueue()
        self.host.configs['shared']['api_key'] = 'replacement'
        self.queue.tick()
        self.assertEqual(len(self.admitted), 4)
        self.assertEqual(len({j['pool'] for j in self.admitted}), 1)

    def test_real_dispatcher_keeps_both_user_limits_under_parallel_execution(self):
        self.thread.stop()
        release = threading.Event()
        lock = threading.Lock()
        active = {}
        maxima = {}
        def worker(kind, tid, config):
            user = str(current_principal()['id']); key = (kind, user)
            with lock:
                active[key] = active.get(key, 0) + 1
                maxima[key] = max(maxima.get(key, 0), active[key])
            try:
                release.wait(5)
                task = self.host.get(kind, tid)
                self.host.save(kind, {**task, 'status': 'succeeded' if kind == 'image_task' else 'done'})
            finally:
                with lock: active[key] -= 1
        self.host.run_image_generation = lambda tid, config: worker('image_task', tid, config)
        self.host.run_provider_task = lambda tid, params, config: worker('task', tid, config)
        for user,model in [('a','shared'),('b','other')]:
            for _ in range(5): self.enqueue(user=user,model=model)
            for _ in range(12): self.enqueue('task',user=user,model=model)
        self.queue.start()
        try:
            deadline = time.monotonic()+5
            while time.monotonic()<deadline and sum(active.values())<30: time.sleep(.02)
            self.assertEqual(sum(active.values()),30)
            self.assertEqual(maxima, {('image_task','a'):5,('image_task','b'):5,('task','a'):10,('task','b'):10})
            release.set()
            deadline = time.monotonic()+5
            while time.monotonic()<deadline and any(j['state'] not in ('done','failed') for j in self.queue.jobs()): time.sleep(.02)
            self.assertTrue(all(j['state']=='done' for j in self.queue.jobs()))
            self.assertTrue(all(value <= 10 for (kind,user),value in maxima.items() if kind == 'task'))
        finally:
            release.set();self.queue.stop()

    def test_images_have_no_account_pool_or_global_64_job_limit(self):
        tids = [self.enqueue() for _ in range(70)]
        self.queue.tick()
        self.assertEqual([job['task_id'] for job in self.admitted], tids)

    def test_stalled_states_expire_at_30_minutes_and_do_not_restart(self):
        for state in ('waiting', 'credentials', 'executing', 'provider', 'unknown'):
            with self.subTest(state=state):
                tid = self.enqueue('task')
                now = time.time()
                task = self.host.get('task', tid)
                self.host.save('task', {**task, 'submit_unknown': state == 'unknown',
                    'provider_task_id': 'upstream' if state == 'provider' else None})
                job = self.queue.patch(self.queue.job('task', tid), state=state, created_at=now-1800)
                with patch('generation_queue.time.time', return_value=now-.01):
                    self.queue.sweep([job])
                self.assertFalse(self.host.get('task', tid).get('queue_timed_out_at'))
                with patch('generation_queue.time.time', return_value=now):
                    self.queue.sweep([self.queue.job('task', tid)])
                expired = self.host.get('task', tid)
                self.assertEqual(expired['status'], 'failed')
                self.assertFalse(expired['submit_unknown'])
                self.assertFalse(expired['polling_interrupted'])
                self.assertEqual(self.queue.job('task', tid)['state'], 'failed')
                self.assertNotIn(tid, self.host.ACTIVE_VIDEO_TASKS)
                self.queue.execute(job)
                self.assertEqual(self.host.calls, [])
                recovered = GenerationQueue(self.host); recovered.recover()
                self.assertEqual(recovered.job('task', tid)['state'], 'failed')

    def test_actual_progress_resets_timeout_but_poll_writes_do_not(self):
        tid = self.enqueue('task');now = time.time()
        job = self.queue.patch(self.queue.job('task', tid), created_at=now-1790)
        self.queue.sweep([job])
        task = self.host.get('task', tid)
        self.host.save('task', {**task, 'status': 'running', 'provider_task_id': 'accepted'})
        with patch('generation_queue.time.time', return_value=now):
            self.queue.sweep([self.queue.job('task', tid)])
        task = self.host.get('task', tid)
        self.host.save('task', {**task, 'updated_at': now+1801, 'last_query_at': now+1801})
        with patch('generation_queue.time.time', return_value=now+1801):
            self.queue.sweep([self.queue.job('task', tid)])
        self.assertEqual(self.host.get('task', tid)['status'], 'failed')

    def test_expired_jobs_release_capacity_for_ten_waiting_videos(self):
        old = [self.enqueue('task') for _ in range(10)]
        self.queue.tick()
        for tid in old:
            self.queue.patch(self.queue.job('task', tid), progress_at=time.time()-1801)
        new = [self.enqueue('task') for _ in range(11)]
        self.queue.tick()
        self.assertEqual([job['task_id'] for job in self.admitted[10:]], new[:10])
        self.assertEqual(self.queue.job('task', new[-1])['state'], 'waiting')


if __name__ == '__main__': unittest.main()
