"""Cancellation races preserve receipts and never restart queued Agent work."""
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import HTTPException
from agent_runtime import CreateRun, Message, StopWork
from test_agent_studio import AgentStudioTests as Fixture


class AgentStopTests(unittest.TestCase):
    setUpClass = Fixture.__dict__['setUpClass']
    tearDownClass = Fixture.__dict__['tearDownClass']
    for name in ('setUp', 'add_media', 'image', 'audio', 'video', 'create', 'step', 'message'):
        locals()[name] = getattr(Fixture, name)
    model_reply = staticmethod(Fixture.model_reply)

    def test_stop_during_text_response_clears_queue_and_blocks_new_turn_until_drained(self):
        rid = self.create('你好')
        entered, release = threading.Event(), threading.Event()
        def delayed(*_):
            entered.set()
            self.assertTrue(release.wait(10))
            return {'choices': [{'message': {'content': '{"intent":"chat","reply":"迟到的回答"}'}}]}
        self.service.active.add(rid)
        with patch.object(self.host, 'llm_json', side_effect=delayed), ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(self.service.work, rid)
            self.assertTrue(entered.wait(10))
            self.message(rid, text='排队但不要执行')
            self.assertEqual(len(self.service.get(rid)['studio_queue']), 1)
            result = self.message(rid, action='cancel')
            self.assertTrue(result['run']['stopping'])
            self.assertFalse(result['run']['studio_queue'])
            with self.assertRaises(HTTPException) as caught:
                self.message(rid, text='停止尚未排空的新消息')
            self.assertEqual(caught.exception.detail['code'], 'agent_stopping')
            release.set()
            worker.result(15)
        final = self.service.snapshot(rid, 1)
        self.assertEqual(final['run']['status'], 'cancelled')
        self.assertFalse(final['run']['stopping'])
        self.assertNotIn('迟到的回答', [event['text'] for event in final['events']])
        operations = [op for op in self.host.listing('agent_operation') if op['run_id'] == rid]
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]['status'], 'succeeded')
        self.assertEqual(operations[0]['result']['reply'], '迟到的回答')
        self.message(rid, text='现在重新问候')
        self.assertEqual(self.step(rid)['status'], 'completed')

    def test_late_media_receipt_is_recorded_then_cancelled_without_followup(self):
        rid = self.create('生成图片')
        entered, release = threading.Event(), threading.Event()
        tid = 'late-image-' + str(uuid.uuid4())
        def submit(request_id):
            task = self.host.save('image_task', {'id': tid, 'request_id': request_id,
                'status': 'queued', 'project_id': self.service.get(rid)['project_id']})
            self.host.generation_queue.enqueue('image_task', task, {'base_url': 'https://fixture.invalid', 'api_key': 'fixture'})
            entered.set()
            self.assertTrue(release.wait(10))
            return task
        def wait():
            try:
                self.service.wait_media(rid, 'late-media', 'image', submit)
            finally:
                self.service.active.discard(rid)
        self.service.active.add(rid)
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(wait)
            self.assertTrue(entered.wait(10))
            self.message(rid, action='cancel')
            self.assertFalse(self.service.get(rid)['media_tasks'])
            release.set()
            with self.assertRaises(StopWork):
                worker.result(15)
        run = self.service.get(rid)
        self.assertEqual(run['status'], 'cancelled')
        self.assertEqual(run['media_tasks']['late-media']['id'], tid)
        self.assertEqual(self.host.get('image_task', tid)['status'], 'cancelled')
        self.assertEqual(self.host.generation_queue.job('image_task', tid)['state'], 'cancelled')
        op = next(op for op in self.host.listing('agent_operation') if op['run_id'] == rid)
        self.assertEqual(op['status'], 'succeeded')
        self.assertFalse([event for event in self.host.listing('agent_event') if event['kind'] in ('media', 'task')])

    def test_terminal_status_cannot_override_stop_for_studio_or_legacy(self):
        for workflow in ('studio', 'video'):
            with self.subTest(workflow=workflow):
                rid = self.service.create(CreateRun(text='小林回家，母亲说欢迎回家。', workflow=workflow,
                    request_id=str(uuid.uuid4())), 1)['run']['id']
                self.service.active.add(rid)
                self.service.patch(rid, queued_text='旧的排队消息')
                result = self.message(rid, action='cancel')
                self.assertTrue(result['run']['stopping'])
                self.assertIsNone(result['run']['queued_text'])
                for status in ('completed', 'waiting_user', 'running', 'waiting_tool'):
                    with self.assertRaises(StopWork):
                        self.service.patch(rid, status=status)
                with self.assertRaises(HTTPException):
                    self.message(rid, text='不要混入旧请求')
                self.service.active.discard(rid)
                self.assertEqual(self.service.get(rid)['status'], 'cancelled')


del Fixture

if __name__ == '__main__':
    unittest.main()
