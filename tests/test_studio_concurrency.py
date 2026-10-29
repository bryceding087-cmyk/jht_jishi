"""Threaded checks for bounded work, quota sharing and durable sibling results."""
from contextlib import ExitStack
from contextvars import ContextVar
from copy import deepcopy
from threading import Barrier, Event, Lock, RLock, Thread, get_ident
from types import SimpleNamespace
import json
import unittest

from agent_runtime import AgentService, StopWork
from agent_studio import StudioAgentMixin
from studio_concurrency import mark_text_repair, run_bounded, text_request_slot


class StudioConcurrencyTests(unittest.TestCase):
    def launch(self, fn):
        errors = []
        def target():
            try:
                fn()
            except BaseException as error:
                errors.append(error)
        thread = Thread(target=target)
        thread.start()
        return thread, errors

    def finish(self, thread, errors, expected=None):
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'bounded task did not drain')
        if expected:
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], expected)
        elif errors:
            raise errors[0]

    def test_three_workers_inherit_context_and_refill_before_slow_sibling(self):
        owner = ContextVar('test_owner')
        first_wave = Barrier(3)
        release_slow, replacement_started = Event(), Event()
        lock = Lock()
        active, maximum = 0, 0
        seen, commits, snapshots, coordinator = [], [], [], []
        def worker(index):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                seen.append((index, owner.get()))
            try:
                if index < 3:
                    first_wave.wait(3)
                if index == 0:
                    self.assertTrue(release_slow.wait(3))
                if index == 3:
                    replacement_started.set()
                mark_text_repair()
                return index * 10
            finally:
                with lock:
                    active -= 1
        def execute():
            owner.set('account-a')
            coordinator.append(get_ident())
            run_bounded(range(7), worker,
                        lambda index, value: commits.append((index, value, get_ident())),
                        checkpoint=lambda: None, progress=snapshots.append)
        thread, errors = self.launch(execute)
        try:
            self.assertTrue(replacement_started.wait(3), 'free slot waited for slow sibling')
        finally:
            release_slow.set()
        self.finish(thread, errors)
        self.assertEqual(maximum, 3)
        self.assertEqual(sorted(seen), [(index, 'account-a') for index in range(7)])
        self.assertEqual(sorted(commits), [(index, index * 10, coordinator[0]) for index in range(7)])
        self.assertEqual(snapshots[-1]['current'], 7)
        self.assertEqual(snapshots[-1]['repaired'], 7)
        self.assertEqual(snapshots[-1]['running'], 0)
        self.assertEqual(snapshots[-1]['queued'], 0)
        self.assertEqual(snapshots[-1]['status'], 'completed')

    def test_failure_stops_new_work_but_commits_successful_siblings(self):
        ready = Barrier(3)
        failed = Event()
        seen, commits, snapshots = [], [], []
        def worker(index):
            seen.append(index)
            ready.wait(3)
            if index == 0:
                failed.set()
                raise ValueError('invalid shot')
            self.assertTrue(failed.wait(3))
            return index
        with self.assertRaisesRegex(ValueError, 'invalid shot'):
            run_bounded(range(10), worker, lambda index, value: commits.append(value),
                        checkpoint=lambda: None, progress=snapshots.append)
        self.assertEqual(sorted(seen), [0, 1, 2])
        self.assertEqual(sorted(commits), [1, 2])
        self.assertEqual(snapshots[-1]['queued'], 7)
        self.assertEqual(snapshots[-1]['failed'], 1)
        self.assertEqual(snapshots[-1]['status'], 'blocked')

    def test_pause_drains_results_and_takes_priority_over_sibling_error(self):
        ready = Barrier(4)
        paused, observed, release = Event(), Event(), Event()
        seen, commits, snapshots = [], [], []
        def checkpoint():
            if paused.is_set():
                observed.set()
                raise StopWork()
        def worker(index):
            seen.append(index)
            ready.wait(3)
            self.assertTrue(release.wait(3))
            if index == 0:
                raise ValueError('sibling failed during pause')
            return index
        thread, errors = self.launch(lambda: run_bounded(range(8), worker,
            lambda index, value: commits.append(value), checkpoint=checkpoint, progress=snapshots.append))
        try:
            ready.wait(3)
            paused.set()
            self.assertTrue(observed.wait(3))
        finally:
            release.set()
        self.finish(thread, errors, StopWork)
        self.assertEqual(sorted(seen), [0, 1, 2])
        self.assertEqual(sorted(commits), [1, 2])
        self.assertEqual(snapshots[-1]['status'], 'paused')

    def test_provider_quota_shared_across_models_but_other_owner_can_run(self):
        ready = Barrier(4)
        release, other_entered = Event(), Event()
        lock = Lock()
        active, maximum = 0, 0
        errors, threads = [], []
        def request(index):
            nonlocal active, maximum
            model = {'id': f'model-{index}', 'base_url': 'https://provider.invalid/v1'}
            with text_request_slot('quota-account', model):
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                    first = active <= 3 and not release.is_set()
                try:
                    if first:
                        ready.wait(3)
                    self.assertTrue(release.wait(3))
                finally:
                    with lock:
                        active -= 1
        for index in range(6):
            thread, captured = self.launch(lambda index=index: request(index))
            threads.append(thread); errors.append(captured)
        try:
            ready.wait(3)
            def other():
                with text_request_slot('other-account', {'base_url': 'https://provider.invalid/v1'}):
                    other_entered.set()
            thread, captured = self.launch(other)
            threads.append(thread); errors.append(captured)
            self.assertTrue(other_entered.wait(3))
        finally:
            release.set()
        for thread, captured in zip(threads, errors):
            self.finish(thread, captured)
        self.assertEqual(maximum, 3)

    def test_paused_quota_waiter_never_enters_and_does_not_leak_slot(self):
        model = {'base_url': 'https://paused.invalid/v1'}
        waiting, paused, entered = Event(), Event(), Event()
        def checkpoint():
            waiting.set()
            if paused.is_set():
                raise StopWork()
        def waiter():
            with text_request_slot('quota-account', model, checkpoint):
                entered.set()
        with ExitStack() as stack:
            for _ in range(3):
                stack.enter_context(text_request_slot('quota-account', model))
            thread, errors = self.launch(waiter)
            self.assertTrue(waiting.wait(3))
            paused.set()
            self.finish(thread, errors, StopWork)
        self.assertFalse(entered.is_set())
        with ExitStack() as stack:
            for _ in range(3):
                stack.enter_context(text_request_slot('quota-account', model))

    def test_optimizer_commits_all_nodes_without_overwrite_and_reuses_cache(self):
        from test_studio_prompt_batches import MemoryBatchService, graph_nodes, DIRECTION
        graph = graph_nodes(19)
        original = deepcopy(graph)
        ready = Barrier(3)
        lock = Lock()
        entered = 0
        def provider(context):
            nonlocal entered
            with lock:
                entered += 1
                first = entered <= 3
            if first:
                ready.wait(3)
            return {'items': [{'node_id': node['node_id'], 'direction': DIRECTION} for node in context['nodes']]}
        service = MemoryBatchService(provider)
        service.studio_optimize_prompts('run', graph)
        self.assertEqual(set(service.run['studio']['prompt_optimizations']), {n['id'] for n in graph['nodes']})
        self.assertEqual(len(service.calls), 4)
        self.assertEqual(service.run['studio']['prompt_optimization_progress']['current'], 19)
        service.studio_optimize_prompts('run', original)
        self.assertEqual(len(service.calls), 4)
        self.assertEqual(original, graph)

    def test_real_studio_llm_shares_three_request_slots_across_two_batches(self):
        # Exercise the actual Studio LLM and operation journal, not a stub of
        # either method. Only persistence and the paid provider are in memory.
        ready = Barrier(4)
        release, all_workers_started = Event(), Event()
        lock = RLock()
        active, peak, entered = 0, 0, 0
        calls, receipts, committed = [], {}, []

        def provider(model, messages):
            nonlocal active, peak
            context = json.loads(messages[-1]['content'])
            with lock:
                calls.append((model['id'], context['run'], context['index']))
                active += 1
                peak = max(peak, active)
                first = len(calls) <= 3
            try:
                if first:
                    ready.wait(3)
                self.assertTrue(release.wait(3))
                return {'value': context['index']}
            finally:
                with lock:
                    active -= 1

        def save(kind, record):
            with lock:
                receipts[(kind, record['id'])] = deepcopy(record)
            return deepcopy(record)

        def raw_record(kind, key):
            with lock:
                return deepcopy(receipts.get((kind, key)))

        class Service(StudioAgentMixin):
            operation = AgentService.operation
            checkpoint = AgentService.checkpoint

            def __init__(self):
                self.runs = {rid: {'id': rid, 'owner_id': 'same-account', 'status': 'running',
                    'studio_job': {'id': f'job-{rid}'}, 'preferences': {'models': {'text': f'model-{rid}'}},
                    'epochs': {}} for rid in ('first', 'second')}
                self.h = SimpleNamespace(
                    internal_registry_model=lambda kind, mid: {
                        'id': mid, 'base_url': 'https://integration-provider.invalid/v1'},
                    llm_json=provider, parse_llm_json=lambda value: value,
                    provider_error_message=str, save=save, raw_record=raw_record)

            def get(self, rid):
                with lock:
                    return deepcopy(self.runs[rid])

            def event(self, *args, **kwargs):
                pass

            def patch(self, rid, **fields):
                with lock:
                    self.runs[rid].update(deepcopy(fields))

        service = Service()

        def batch(rid):
            def worker(index):
                nonlocal entered
                with lock:
                    entered += 1
                    if entered == 6:
                        all_workers_started.set()
                return service.studio_llm(rid, f'part-{index}', '拆解场次', '返回 JSON',
                                         {'run': rid, 'index': index})
            run_bounded(range(6), worker, lambda index, result: committed.append((rid, index, result)),
                        checkpoint=lambda: service.checkpoint(rid))

        threads = [self.launch(lambda rid=rid: batch(rid)) for rid in ('first', 'second')]
        try:
            ready.wait(3)
            self.assertTrue(all_workers_started.wait(3))
            with lock:
                self.assertEqual(active, 3)
                # Three other workers wait before journaling, not as unknown
                # or submitting requests that have never reached the provider.
                self.assertEqual(len(receipts), 3)
        finally:
            release.set()
        for thread, errors in threads:
            self.finish(thread, errors)
        self.assertEqual(peak, 3)
        self.assertEqual(len(calls), 12)
        self.assertEqual({call[0] for call in calls}, {'model-first', 'model-second'})
        self.assertEqual(len(committed), 12)
        self.assertEqual(len(receipts), 12)
        self.assertTrue(all(op['status'] == 'succeeded' for op in receipts.values()))
        for rid in ('first', 'second'):
            self.assertEqual(service.studio_llm(rid, 'part-0', '拆解场次', '返回 JSON',
                {'run': rid, 'index': 0}), {'value': 0})
        self.assertEqual(len(calls), 12, 'completed operation must reuse its response')


if __name__ == '__main__':
    unittest.main()
