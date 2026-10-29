"""Canvas commands run against disposable storage; no paid provider calls."""
from copy import deepcopy
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from agent_runtime import CreateRun
from canvas_tools import CanvasOperation, execute_graph, submit_node
from agent_studio import StudioPlan
import test_agent_runtime as fixture


class CanvasToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture.AgentRuntimeTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()
    setUp = fixture.AgentRuntimeTests.setUp
    model_reply = fixture.AgentRuntimeTests.model_reply
    image = fixture.AgentRuntimeTests.image
    audio = fixture.AgentRuntimeTests.audio
    video = fixture.AgentRuntimeTests.video
    add_media = fixture.AgentRuntimeTests.add_media

    def graph_run(self):
        graph = self.host.create_project(self.host.Project(name='当前画布', nodes=[
            {'id':'a','type':'prompt','title':'A','text':'原文','x':100,'y':100},
            {'id':'b','type':'asset','title':'B','x':500,'y':100,'image_settings':{'prompt':'一只橘猫'}},
        ], edges=[]))
        rid = self.service.create(CreateRun(text='操作当前画布', project_id=graph['id'],request_id=str(uuid.uuid4())), 1)['run']['id']
        return graph, self.service.get(rid)

    def command(self, run, revision, operations, key='request'):
        return execute_graph(self.host, run, [CanvasOperation(**op).model_dump() for op in operations], revision, key)

    def test_graph_batch_and_idempotency(self):
        graph, run = self.graph_run()
        ops=[{'name':'rename','args':{'title':'改名后'}},
             {'name':'update_node','args':{'node_id':'a','changes':{'x':240,'text':'新原文'}}},
             {'name':'add_node','args':{'node':{'id':'c','type':'video','title':'镜头','settings':{'prompt':'橘猫转头'}}}},
             {'name':'connect','args':{'from':'a','to':'c'}},
             {'name':'create_group','args':{'node_ids':['a','b'],'title':'参考组','color':'#4285f4'}},
             {'name':'select','args':{'node_ids':['a']}}, {'name':'zoom','args':{'value':.7}}]
        result=self.command(run,graph['revision'],ops)
        saved=self.host.active_project(graph['id'])
        self.assertEqual(saved['name'],'改名后')
        self.assertEqual(saved['nodes'][0]['settings']['prompt'],'新原文')
        self.assertEqual(saved['edges'],[{'from':'a','to':'c'}])
        self.assertEqual(saved['groups'][0]['node_ids'],['a','b'])
        self.assertEqual(len(result['ui']),2)
        self.assertEqual(self.command(run,graph['revision'],ops),result)
        self.assertEqual(self.host.active_project(graph['id'])['revision'],saved['revision'])

    def test_batch_failure_and_version_conflict_do_not_partially_write(self):
        graph,run=self.graph_run()
        with self.assertRaises(HTTPException):
            self.command(run,graph['revision'],[{'name':'rename','args':{'title':'不应保存'}},{'name':'update_node','args':{'node_id':'foreign','changes':{'text':'bad'}}}])
        self.assertEqual(self.host.active_project(graph['id'])['name'],graph['name'])
        self.host.update_project(graph['id'],self.host.Project(**{**graph,'name':'用户新修改'}))
        with self.assertRaises(HTTPException) as error:
            self.command(run,graph['revision'],[{'name':'clear','args':{}}])
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(len(self.host.active_project(graph['id'])['nodes']),2)

    def test_scope_cannot_be_overridden_even_for_same_account_canvas(self):
        graph,run=self.graph_run()
        other=self.host.create_project(self.host.Project(name='另一画布',nodes=[{'id':'foreign','type':'prompt','x':0,'y':0}]))
        attacks=[{'name':'clear','args':{'project_id':other['id']}},
                 {'name':'update_node','args':{'node_id':'foreign','changes':{'text':'bad'}}},
                 {'name':'update_node','args':{'node_id':'a','changes':{'mujian':{'runId':'foreign'}}}},
                 {'name':'update_node','args':{'node_id':'a','changes':{'settings':{'base_url':'https://outside.invalid'}}}},
                 {'name':'panel','args':{'value':'models'}},
                 {'name':'pan','args':{'x':float('nan'),'y':0}}]
        for i,op in enumerate(attacks):
            with self.subTest(op=op),self.assertRaises(HTTPException): self.command(run,graph['revision'],[op],str(i))
        self.assertEqual(self.host.active_project(other['id'])['nodes'][0].get('text'),None)
        with self.assertRaises(ValidationError): CanvasOperation(name='execute_js',args={'code':'bad'})
        with self.assertRaises(HTTPException): self.command({'id':'unbound','owner_id':'1','project_id':other['id'],'creation_type':'conversation'},1,[{'name':'clear','args':{}}])

    def test_foreign_asset_cannot_be_bound_without_explicit_attachment(self):
        graph,run=self.graph_run()
        asset=self.add_media('image','外部素材','')
        with self.assertRaises(HTTPException) as error:
            self.command(run,graph['revision'],[{'name':'bind_asset','args':{'node_id':'b','asset_id':asset['id']}}])
        self.assertEqual(error.exception.status_code,403)
        result=execute_graph(self.host,run,[{'name':'bind_asset','args':{'node_id':'b','asset_id':asset['id']}}],graph['revision'],'attached',[asset['id']])
        self.assertTrue(result['changed'])

    def test_delete_duplicate_arrange_and_undo_redo(self):
        graph,run=self.graph_run()
        original=deepcopy(graph['nodes'])
        first=self.command(run,graph['revision'],[{'name':'duplicate_nodes','args':{'node_ids':['a']}},{'name':'arrange','args':{'layout':'horizontal'}}])
        self.assertEqual(len(self.host.active_project(graph['id'])['nodes']),3)
        undo=self.command(run,first['after_revision'],[{'name':'undo','args':{}}],'undo')
        self.assertEqual(self.host.active_project(graph['id'])['nodes'],original)
        redo=self.command(run,undo['after_revision'],[{'name':'redo','args':{}}],'redo')
        self.assertEqual(len(self.host.active_project(graph['id'])['nodes']),3)
        self.command(run,redo['after_revision'],[{'name':'delete_nodes','args':{'node_ids':['a']}}],'delete')
        self.assertNotIn('a',[n['id'] for n in self.host.active_project(graph['id'])['nodes']])

    def test_generation_is_pinned_and_retry_does_not_duplicate_submission(self):
        graph,run=self.graph_run()
        job={'kind':'canvas','id':'generate-request','text':'生成B','canvas_revision':graph['revision'],'plan':{'operations':[{'name':'generate','args':{'node_ids':['b']}}]}}
        self.service.patch(run['id'],studio_job=job,status='running')
        requests=[]
        def submit(request):
            requests.append(request)
            return {'id':'queued-image','status':'queued','project_id':request.project_id,'node_id':request.node_id}
        with patch.object(self.host,'create_image_generation',side_effect=submit):
            self.service.work(run['id'])
            self.assertEqual(self.service.get(run['id'])['status'],'completed',self.service.get(run['id']).get('error'))
            self.service.patch(run['id'],studio_job=deepcopy(job),status='running')
            self.service.work(run['id'])
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0].project_id,graph['id'])
        self.assertEqual(requests[0].node_id,'b')
        self.assertEqual(self.host.active_project(graph['id'])['nodes'][1]['image_settings']['image_task_id'],'queued-image')
        events=[e for e in self.host.listing('agent_event') if e['kind']=='canvas_command']
        self.assertEqual(len(events),1)

    def test_canvas_planner_schema_keeps_operations_and_rejects_unknown_tool(self):
        plan=StudioPlan(intent='canvas',operations=[{'name':'fit','args':{'node_ids':'all'}}])
        self.assertEqual(plan.operations[0].name,'fit')
        with self.assertRaises(ValidationError): StudioPlan(intent='canvas',operations=[{'name':'global_settings','args':{}}])

    def test_invalid_generation_parameters_fail_before_graph_mutation_or_submission(self):
        graph,run=self.graph_run()
        with patch.object(self.host,'create_image_generation') as submit:
            with self.assertRaises((HTTPException,ValidationError)):
                self.command(run,graph['revision'],[{'name':'rename','args':{'title':'不能部分保存'}},
                    {'name':'update_node','args':{'node_id':'b','changes':{'image_settings':{'count':10}}}},
                    {'name':'generate','args':{'node_ids':['b']}}])
            submit.assert_not_called()
        self.assertEqual(self.host.active_project(graph['id'])['name'],graph['name'])

    def test_repeated_undo_redo_and_manual_edit_guard(self):
        graph,run=self.graph_run()
        def apply(ops,key):
            current=self.host.active_project(graph['id'])
            return self.command(run,current['revision'],ops,key)
        apply([{'name':'rename','args':{'title':'第一次'}}],'first')
        apply([{'name':'rename','args':{'title':'第二次'}}],'second')
        apply([{'name':'undo','args':{}}],'undo-second')
        apply([{'name':'undo','args':{}}],'undo-first')
        self.assertEqual(self.host.active_project(graph['id'])['name'],'当前画布')
        apply([{'name':'redo','args':{}}],'redo-first')
        self.assertEqual(self.host.active_project(graph['id'])['name'],'第一次')
        apply([{'name':'redo','args':{}}],'redo-second')
        current=self.host.active_project(graph['id'])
        self.host.update_project(graph['id'],self.host.Project(**{**current,'name':'手动编辑'}))
        with self.assertRaises(HTTPException): apply([{'name':'undo','args':{}}],'guard')
        self.assertEqual(self.host.active_project(graph['id'])['name'],'手动编辑')

    def test_bulk_move_preserves_spacing_and_moves_the_group_frame(self):
        graph,run=self.graph_run()
        self.command(run,graph['revision'],[{'name':'create_group','args':{'group_id':'g','node_ids':['a','b']}},
            {'name':'update_group','args':{'group_id':'g','changes':{'x':80,'y':80,'width':800,'height':400}}},
            {'name':'move_nodes','args':{'node_ids':'all','dx':200,'dy':-50}},
            {'name':'update_nodes','args':{'node_ids':['a','b'],'changes':{'title':'批量改名'}}}])
        updated=self.host.active_project(graph['id'])
        self.assertEqual(updated['nodes'][1]['x']-updated['nodes'][0]['x'],400)
        self.assertEqual(updated['nodes'][0]['x'],300)
        self.assertEqual(updated['groups'][0]['x'],280)
        self.assertTrue(all(n['title']=='批量改名' for n in updated['nodes']))


if __name__=='__main__': unittest.main()
