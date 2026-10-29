"""Conversation recycle lifecycle, ownership and retained outputs."""
from test_sidebar_metadata import SidebarMetadataTests


class ConversationRecycleTests(SidebarMetadataTests):
    def create_chat(self, kind='image'):
        return self.request('POST', '/api/conversations', {
            'title': '可恢复对话', 'kind': kind, 'prompt': '测试', 'request_id': 'recycle-'+kind,
        })[2]

    def test_trash_restore_preserves_output_and_metadata(self):
        item=self.create_chat(); cid=item['id']; path='/api/conversations/'+cid
        self.host.save('asset', {'id':'kept-output','project_id':cid,'kind':'image','url':'/uploads/test.png','source':'generated','owner_id':item['owner_id']})
        self.assertEqual(self.request('GET',path+'/info')[0],200)
        self.assertEqual(self.request('DELETE',path)[0],204)
        self.assertEqual(self.request('GET',path)[0],404)
        self.assertEqual(self.request('GET','/api/conversations')[2],[])
        self.assertEqual([c['id'] for c in self.request('GET','/api/conversations?trashed=true')[2]],[cid])
        self.assertEqual(self.request('PATCH',path+'/metadata',{'title':'hidden'})[0],404)
        self.assertFalse(self.host.get('asset','kept-output').get('deleted_at'))
        self.assertEqual(self.request('POST',path+'/restore')[0],200)
        self.assertEqual(self.request('GET',path)[2]['title'],item['title'])
        self.assertEqual(self.request('GET','/api/conversations?trashed=true')[2],[])
        self.assertEqual(self.request('GET','/api/conversations')[2][0]['cover']['url'],'/uploads/test.png')

    def test_purge_requires_trash_and_prevents_restore(self):
        item=self.create_chat(); path='/api/conversations/'+item['id']
        self.assertEqual(self.request('DELETE',path+'?permanent=true')[0],409)
        self.assertEqual(self.request('DELETE',path)[0],204)
        self.assertEqual(self.request('DELETE',path+'?permanent=true')[0],204)
        self.assertEqual(self.request('POST',path+'/restore')[0],404)
        self.assertEqual(self.request('GET','/api/conversations?trashed=true')[2],[])

    def test_member_cannot_access_other_owners_recycle_or_info(self):
        item=self.create_chat(); path='/api/conversations/'+item['id']
        self.assertEqual(self.request('DELETE',path)[0],204)
        self.login_as_member()
        self.assertEqual(self.request('GET','/api/conversations?trashed=true')[2],[])
        for method,suffix in [('GET','/info'),('DELETE',''),('DELETE','?permanent=true'),('POST','/restore')]:
            self.assertEqual(self.request(method,path+suffix)[0],404)

    def test_agent_canvas_link_and_history_survive_restore_without_restart(self):
        item=self.create_chat(); cid=item['id']; path='/api/conversations/'+cid
        self.host.save('conversation',{**self.host.get('conversation',cid),'kind':'agent','run_id':'test-run'})
        self.host.save('agent_run',{'id':'test-run','project_id':cid,'owner_id':item['owner_id'],'status':'running','updated_at':1,'studio':{'canvas_project_id':'test-canvas'}})
        self.assertEqual(self.request('GET','/api/conversations')[2][0]['canvas_project_id'],'test-canvas')
        self.assertEqual(self.request('DELETE',path)[0],204)
        self.assertEqual(self.host.get('agent_run','test-run')['status'],'cancelled')
        self.assertEqual(self.request('POST',path+'/restore')[0],200)
        run=self.host.get('agent_run','test-run')
        self.assertFalse(run.get('deleted_at'))
        self.assertEqual(run['status'],'cancelled')
        self.assertTrue(run['stop_requested'])
