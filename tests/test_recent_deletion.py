import io
from PIL import Image
from test_sidebar_metadata import SidebarMetadataTests


class RecentDeletionTests(SidebarMetadataTests):
    def output(self, pid):
        data=io.BytesIO();Image.new('RGB',(8,8),'blue').save(data,format='PNG')
        return self.host.store_uploaded_asset(data.getvalue(),'output.png',pid)

    def test_canvas_delete_cannot_restore_and_preserves_assets_and_tasks(self):
        _,_,project=self.request('POST','/api/projects',{'name':'delete canvas','nodes':[],'edges':[]})
        asset=self.output(project['id'])
        self.host.save('image_task',{'id':'keep-task','project_id':project['id'],'status':'running'})
        self.assertEqual(self.browser.request('DELETE','/api/recent-items/project/'+project['id'])[0],204)
        self.assertNotIn(project['id'],[p['id'] for p in self.request('GET','/api/projects')[2]])
        self.assertNotIn(project['id'],[p['id'] for p in self.request('GET','/api/projects?trashed=true')[2]])
        self.assertEqual(self.browser.request('POST','/api/projects/'+project['id']+'/restore')[0],404)
        self.assertEqual(self.host.require_live_asset(asset['id'])['id'],asset['id'])
        self.assertEqual(self.host.get('image_task','keep-task')['status'],'running')

    def test_conversation_delete_preserves_generated_assets_and_stops_agent(self):
        _,_,conversation=self.request('POST','/api/conversations',{'title':'delete chat','kind':'image','prompt':'test','settings':{},'request_id':'delete-chat'})
        cid=conversation['id'];asset=self.output(cid)
        self.host.save('agent_run',{'id':'delete-agent','project_id':cid,'owner_id':'1','status':'running','updated_at':1})
        self.assertEqual(self.browser.request('DELETE','/api/recent-items/conversation/'+cid)[0],204)
        self.assertNotIn(cid,[p['id'] for p in self.request('GET','/api/conversations')[2]])
        self.assertEqual(self.browser.request('GET','/api/conversations/'+cid)[0],404)
        self.assertEqual(self.host.require_live_asset(asset['id'])['id'],asset['id'])
        run=self.host.get('agent_run','delete-agent')
        self.assertEqual(run['status'],'cancelled');self.assertTrue(run['stop_requested'])
        self.assertEqual(self.browser.request('GET','/api/agent/runs/delete-agent')[0],404)

    def test_cannot_delete_another_users_conversation(self):
        _,_,conversation=self.request('POST','/api/conversations',{'title':'private','kind':'image','prompt':'test','settings':{},'request_id':'private-chat'})
        cid=conversation['id'];self.login_as_member()
        self.assertEqual(self.browser.request('DELETE','/api/recent-items/conversation/'+cid)[0],404)
        self.assertFalse(self.host.get('conversation',cid).get('deleted_at'))
