import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_user_isolation as isolation
from generation_queue import GenerationQueue


class QueueAPITests(unittest.TestCase):
    login = isolation.UserIsolationTests.login
    request = isolation.UserIsolationTests.request
    upload = isolation.UserIsolationTests.upload

    @classmethod
    def setUpClass(cls): isolation.UserIsolationTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): isolation.UserIsolationTests.tearDownClass.__func__(cls)

    def setUp(self):
        isolation.UserIsolationTests.setUp(self)
        self.h.ACTIVE_VIDEO_TASKS.clear()
        self.h.generation_queue.workers.clear()
        for kind,mid in [('image',self.h.DEFAULT_IMAGE_MODEL_ID),('video',self.h.DEFAULT_MODEL_ID)]:
            self.h.update_registry_model(kind,mid,self.h.RegistryModelUpdate(api_key='fixture-key'))

    def context(self, browser, kind):
        return self.request(browser,'POST','/api/conversations',{'kind':kind,'title':'排队验证','prompt':'test','request_id':kind},201)['id']

    def image_task(self, browser, cid, index):
        return self.request(browser,'POST','/api/images/generations',{'project_id':cid,'prompt':'排队 '+str(index),'request_id':str(index)},202)

    def test_video_limits_are_reported_and_shared_key_users_get_separate_slots(self):
        tasks = []
        for browser in (self.a, self.b):
            cid = self.context(browser, 'video')
            owned = [self.request(browser, 'POST', '/api/tasks', {
                'project_id': cid, 'node_id': 'conversation-video', 'prompt': '视频并发验证',
                'model_config_id': self.h.DEFAULT_MODEL_ID, 'request_id': str(index)}, 202)
                for index in range(11)]
            tasks.append(owned)
        admitted = []
        with patch('generation_queue.threading.Thread', side_effect=lambda **kw: SimpleNamespace(start=lambda: admitted.append(kw['args'][0]))):
            self.h.generation_queue.tick()
        self.assertEqual(len(admitted), 20)
        for browser, owned in zip((self.a, self.b), tasks):
            queue = self.request(browser, 'GET', '/api/generation-queue')
            self.assertEqual(queue['limits'], {'image': None, 'video': 10})
            self.assertEqual(queue['global_limits'], {'image': None, 'video': 100})
            self.assertEqual({task['id'] for task in queue['tasks']}, {task['id'] for task in owned})
            self.assertEqual(sum(task['state'] == 'executing' for task in queue['tasks']), 10)
            waiting = self.request(browser, 'GET', '/api/tasks/' + owned[-1]['id'])
            self.assertEqual(waiting['queue_state'], 'waiting')
            self.assertEqual(waiting['queue_limit'], 10)
            self.assertEqual(waiting['queue_global_limit'], 100)
            self.assertNotIn('fixture-key', str(queue))

    def test_multiple_submissions_in_one_conversation_and_idempotent_receipts(self):
        cid = self.context(self.a,'image')
        tasks = [self.image_task(self.a,cid,i) for i in range(5)]
        repeated = self.image_task(self.a,cid,0)
        self.assertEqual(repeated['id'],tasks[0]['id'])
        state = self.request(self.a,'GET','/api/conversations/generation-state?conversation_id='+cid)
        self.assertTrue(state['is_busy']); self.assertTrue(state['allow_queue'])
        queue = self.request(self.a,'GET','/api/generation-queue')
        self.assertEqual(len(queue['tasks']),5)
        self.assertEqual(self.request(self.a,'GET','/api/images/generations/'+tasks[-1]['id'])['queue_ahead'],4)
        self.assertNotIn('fixture-key',str(queue))
        admitted=[]
        with patch('generation_queue.threading.Thread',side_effect=lambda **kw:SimpleNamespace(start=lambda:admitted.append(kw['args'][0]))):
            self.h.generation_queue.tick()
        self.assertEqual(len(admitted),5)

    def test_queue_access_and_cancellation_are_account_scoped(self):
        cid=self.context(self.a,'image'); task=self.image_task(self.a,cid,0)
        self.assertEqual(self.request(self.b,'GET','/api/generation-queue')['tasks'],[])
        self.request(self.b,'POST','/api/generation-queue/image_task/'+task['id']+'/cancel',{},404)
        result=self.request(self.a,'POST','/api/generation-queue/image_task/'+task['id']+'/cancel',{})
        self.assertEqual(result['status'],'cancelled')
        self.assertFalse(result['can_cancel_queue'])
        self.assertEqual(self.request(self.a,'GET','/api/generation-queue')['tasks'],[])
        self.assertEqual(self.request(self.a,'GET','/api/images/generations/'+task['id'])['status'],'cancelled')

    def test_waiting_receipts_survive_restart_without_being_failed_or_resubmitted(self):
        cid=self.context(self.a,'image'); task=self.image_task(self.a,cid,0)
        self.h.ACTIVE_IMAGE_TASKS.clear()
        queue=GenerationQueue(self.h);queue.recover()
        current=self.request(self.a,'GET','/api/images/generations/'+task['id'])
        self.assertEqual(current['status'],'queued')
        self.assertEqual(current['queue_state'],'waiting')
        self.assertIn(task['id'],self.h.ACTIVE_IMAGE_TASKS)


if __name__=='__main__':unittest.main()
