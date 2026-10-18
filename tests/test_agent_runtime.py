"""Agent lifecycle and real local rendering against disposable application data.

Provider responses are substituted. No network generation or workspace database
is used by these tests.
"""
import importlib.util
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import wave
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from agent_media import execute, inspect_asset, ass_text
from agent_runtime import CreateRun, Message, Preferences
from test_auth import Browser
from auth_service import hash_password


class AgentRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix='jht-agent-test-')
        spec=importlib.util.spec_from_file_location('agent_test_app',Path(__file__).resolve().parents[1]/'app.py')
        cls.host=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'JIANHUITONG_DATA_DIR':cls.directory.name}):
            spec.loader.exec_module(cls.host)
        if cls.host.DATA!=Path(cls.directory.name):
            raise RuntimeError('Refusing to use the workspace database')
        cls.video_submit_original=staticmethod(cls.host.submit_video_task)
        cls.clip=cls.host.DATA/'fixture.mp4'
        execute(['-f','lavfi','-i','color=c=0x374c60:s=320x180:r=24:d=4',
                 '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','4',
                 '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(cls.clip)])

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        with self.host.connect() as db:
            db.execute('DELETE FROM records')
        self.service=self.host.app.state.agent_service
        self.service.active.clear()
        self.kick=patch.object(self.service,'kick').start()
        self.addCleanup(patch.stopall)
        self.provider_calls=[]
        patch.object(self.host,'llm_json',side_effect=self.model_reply).start()
        patch.object(self.host,'create_image_generation',side_effect=self.image).start()
        patch.object(self.host,'create_audio_generation',side_effect=self.audio).start()
        patch.object(self.host,'submit_video_task',side_effect=self.video).start()

    def model_reply(self,config,messages):
        prompt=messages[0]['content']
        context=json.loads(messages[-1]['content'])
        if '返回 {complete:bool' in prompt:
            value={'complete':'缺项' not in context['story'],'summary':'人物、事件和结尾表达明确。','gaps':['缺少事件结果'],
                   'title':'回家','locked_lines':[{'text':'欢迎回家。','speaker':'母亲','kind':'dialogue'}],'brand':'一起回家。'}
        elif 'estimated_duration:number' in prompt:
            value={'summary':'两场画面配合一句对白。','estimated_duration':8,'segments':[{'summary':'回家','seconds':4},{'summary':'相拥','seconds':4}],'notes':'台词待实测'}
        elif 'visuals:[{id' in prompt:
            value={'title':'回家','document':'# 创意设计\n\n小林回家，母亲说“欢迎回家。”，两人相拥。片尾：一起回家。\n\n## 视觉方向\n\n- **暖光**与自然色调\n- 克制的镜头运动\n\n| 场次 | 情绪 |\n| --- | --- |\n| 回家 | 期待 |\n| 相拥 | 温暖 |',
                   'visuals':[{'id':'v','name':'温暖的家','prompt':'温暖写实的家庭门口','category':'场景','asset_id':''}],
                   'speakers':[{'name':'母亲','description':'温暖自然的成年女声','asset_id':''}],'music_direction':'温暖钢琴'}
        elif '返回 {passed:bool' in prompt:
            value={'passed':True,'summary':'台词与动作完整保留。','issues':[]}
        elif 'scenes:[{id,duration' in prompt:
            value={'title':'回家','summary':'两段，共八秒。','scenes':[
                {'id':'s1','duration':4,'description':'小林走到门口','camera':'缓慢推进','lighting':'暖光','prompt':'小林回到家门口，暖光远景','line_ids':['line_1'],'visual_ids':['visual_1']},
                {'id':'s2','duration':4,'description':'两人相拥','camera':'固定','lighting':'暖光','prompt':'母子相拥，写实远景','line_ids':[],'visual_ids':['visual_1']}], 'music_prompt':'温暖钢琴纯音乐'}
        elif '完整保留原文台词' in prompt:
            value={'text':'小林回到家，母亲说：“欢迎回家。”两人相拥。片尾：一起回家。','changes':['补充人物相拥的结尾']}
        elif '处理用户修改' in prompt:
            value={'kind':'brand','brand':'温暖常在。','changes':[],'summary':'仅更新片尾文字，其他素材继续复用。'}
        else:
            raise AssertionError('Unhandled creative prompt')
        return {'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)}}]}

    def add_media(self,kind,name,project_id):
        if kind=='image':
            image=Image.new('RGB',(160,90),(70,100,120)); stream=io.BytesIO();image.save(stream,'PNG'); data=stream.getvalue();name+='.png'
        elif kind=='audio':
            stream=io.BytesIO()
            with wave.open(stream,'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(48000);audio.writeframes(b'\0\0'*24000)
            data=stream.getvalue();name+='.wav'
        else:
            data=self.clip.read_bytes();name+='.mp4'
        return self.host.store_uploaded_asset(data,name,project_id,source='generated')

    def image(self,request):
        self.provider_calls.append('image')
        asset=self.add_media('image',request.name,request.project_id)
        return self.host.save('image_task',{'id':'image-'+str(uuid.uuid4()),'status':'succeeded','asset':asset})

    def audio(self,request):
        self.provider_calls.append(request.mode)
        asset=self.add_media('audio',request.name,request.project_id)
        return self.host.save('audio_task',{'id':'audio-'+str(uuid.uuid4()),'status':'succeeded','asset':asset,'duration':.5})

    def video(self,request,project_snapshot=None):
        self.provider_calls.append('video')
        asset=self.add_media('video','分镜片段',request.project_id)
        tid=str(uuid.uuid4())
        self.host.save('asset',{**self.host.get('asset',asset['id']),'task_id':tid})
        return self.host.save('task',{'id':tid,'project_id':request.project_id,'status':'done',
                                     'video_url':asset['url'],'params':request.model_dump()})

    def create(self,text=None,preferences=None,project_id=None):
        body=CreateRun(text=text or '小林回到家，母亲说：“欢迎回家。”两人相拥。片尾：一起回家。',
                       request_id=str(uuid.uuid4()),preferences=preferences or Preferences(),project_id=project_id)
        return self.service.create(body,1)['run']['id']

    def step(self,rid):
        self.service.work(rid)
        return self.service.get(rid)

    def confirm(self,rid,action='confirm',values=None):
        review=self.service.get(rid)['pending_review']
        self.assertIsNotNone(review,self.service.get(rid).get('error'))
        self.service.message(rid,Message(request_id=str(uuid.uuid4()),action=action,review_id=review['id'],values=values or {}),1)

    def to_storyboard(self,rid):
        self.assertEqual(self.step(rid)['pending_review']['gate'],'G1')
        self.confirm(rid)
        self.assertEqual(self.step(rid)['pending_review']['gate'],'G2')
        self.confirm(rid)
        self.assertEqual(self.step(rid)['pending_review']['gate'],'G3')

    def test_visual_style_survives_review_and_reaches_image_and_video_requests(self):
        rid=self.create(preferences=Preferences(voice=False,music=False,visual_style={'id':'feature-animation'}))
        run=self.step(rid)
        self.assertEqual(run['pending_review']['gate'],'G1')
        self.assertNotIn('style',[q['key'] for q in run['pending_review']['questions']])
        self.confirm(rid)
        self.assertEqual(self.step(rid)['pending_review']['gate'],'G2')
        for call in self.host.create_image_generation.call_args_list:
            self.assertEqual(call.args[0].visual_style.id,'feature-animation')
        self.assertTrue(self.host.create_image_generation.called)
        self.confirm(rid)
        self.assertEqual(self.step(rid)['pending_review']['gate'],'G3')
        self.confirm(rid)
        with patch('agent_runtime.render_timeline',side_effect=lambda *args,**kwargs:self.add_media('video','模拟成片',run['project_id'])):
            self.step(rid)
        self.assertTrue(self.host.submit_video_task.called)
        for call in self.host.submit_video_task.call_args_list:
            self.assertEqual(call.args[0].visual_style.id,'feature-animation')

    def test_character_visuals_are_images_and_resume_without_retired_character_api(self):
        original = self.model_reply
        def reply(config, messages):
            result = original(config, messages)
            value = json.loads(result['choices'][0]['message']['content'])
            if 'visuals' in value:
                value['visuals'][0]['category'] = 'character'
            result['choices'][0]['message']['content'] = json.dumps(value, ensure_ascii=False)
            return result
        with patch.object(self.host, 'llm_json', side_effect=reply), patch.object(self.host, 'create_character', side_effect=AssertionError('retired API')) as retired:
            rid = self.create()
            self.assertEqual(self.step(rid)['pending_review']['gate'], 'G1')
            self.confirm(rid)
            self.assertEqual(self.step(rid)['pending_review']['gate'], 'G2')
            run = self.service.get(rid)
            graph = self.host.creation_context(run['project_id'])
            self.assertEqual(run['creation_type'], 'conversation')
            self.assertIsNone(self.host.raw_record('project', run['project_id']))
            self.assertFalse(any(node['type'] == 'character' for node in graph['nodes']))
            binding = run['bindings']['visual_1']
            self.assertEqual(self.host.require_live_asset(binding['asset_id'])['kind'], 'image')
            calls = list(self.provider_calls)
            self.service.patch(rid, status='running', phase='assets', pending_review=None)
            self.service.phase_assets(rid)
            self.assertEqual(self.provider_calls, calls)
            retired.assert_not_called()

    def test_full_production_persists_media_canvas_and_real_render(self):
        rid=self.create();self.to_storyboard(rid)
        run=self.service.get(rid)
        creative=run['artifacts']['creative']['payload']
        self.assertEqual(len(creative['bindings']),2)
        project=self.host.creation_context(run['project_id'])
        self.assertEqual(len([n for n in project['nodes'] if n['type']=='video']),0)
        self.assertEqual(set(self.service.video_inputs(rid,project)),{'shot_1','shot_2'})
        self.assertEqual(project,self.host.creation_context(run['project_id']))
        self.confirm(rid)
        run=self.step(rid)
        self.assertEqual(run['status'],'waiting_user',run.get('error'))
        self.assertEqual(run['pending_review']['gate'],'G4')
        graph=self.host.creation_context(run['project_id'])
        nodes={n['agent_key']:n for n in graph['nodes'] if n.get('agent_run_id')==rid}
        pairs={(e['from'],e['to']) for e in graph['edges']}
        self.assertEqual(len([n for n in graph['nodes'] if n['type']=='video']),len(run['videos']))
        self.assertEqual(len([n for n in graph['nodes'] if n['type']=='video-asset']),1)
        self.assertEqual(nodes['final']['assetId'],run['render_asset']['id'])
        self.assertEqual(len([key for key in nodes if key in ('source','working')]),1)
        self.assertFalse(any(key.startswith(('doc:','result:')) for key in nodes))
        self.assertFalse(any(e.get('relation')=='agent-flow' for e in graph['edges']))
        self.assertTrue(all('agent_stage' not in n for n in graph['nodes']))
        for shot_id,video in run['videos'].items():
            self.assertEqual(nodes[shot_id]['assetId'],video['asset_id'])
            self.assertIn((nodes['script']['id'],nodes[shot_id]['id']),pairs)
        for name in ('director','analysis','creative','storyboard','music_brief','timeline','quality'):
            artifact=run['artifacts'][name]
            self.assertEqual(self.host.get('agent_artifact',artifact['id'])['payload'],artifact['payload'])
        self.assertLess(self.provider_calls.index('music'),self.provider_calls.index('video'))
        result=inspect_asset(self.host,run['render_asset']['id'])
        self.assertAlmostEqual(result['duration'],8,delta=.2)
        self.assertEqual((result['width'],result['height']),(1280,720))
        self.confirm(rid)
        self.assertEqual(self.service.get(rid)['status'],'completed')
        paid_before=list(self.provider_calls)
        self.service.apply_revision(rid,'片尾文字改成温暖常在。')
        self.step(rid)
        self.assertEqual(self.provider_calls,paid_before)
        self.assertEqual(self.service.get(rid)['artifacts']['timeline']['payload']['brand'],'温暖常在。')
        revised=self.host.creation_context(run['project_id'])
        self.assertEqual(len([n for n in revised['nodes'] if n['type']=='video']),2)
        self.assertEqual(len([n for n in revised['nodes'] if n['type']=='video-asset']),1)

    def test_incomplete_story_requires_active_polishing_and_adoption(self):
        rid=self.create('缺项：母亲说“欢迎回家。”片尾：一起回家。')
        self.assertEqual(self.step(rid)['pending_review']['gate'],'S0')
        original=self.service.get(rid)['working_story']
        self.confirm(rid,'polish');self.step(rid)
        run=self.service.get(rid)
        self.assertEqual(run['working_story'],original)
        self.assertEqual(run['pending_review']['gate'],'DRAFT')
        self.assertFalse(self.provider_calls)
        self.confirm(rid,'adopt');self.step(rid)
        self.assertNotEqual(self.service.get(rid)['working_story'],original)
        self.assertEqual(self.service.get(rid)['pending_review']['gate'],'G1')

    def test_legacy_canvas_migrates_without_changing_generation_or_manual_work(self):
        canvas=self.host.create_project(self.host.Project(name='手动创建的画布'))
        rid=self.create(project_id=canvas['id']);self.to_storyboard(rid);self.confirm(rid);self.step(rid)
        run=self.service.get(rid)
        graph=self.host.creation_context(run['project_id'])
        before_inputs=self.service.video_inputs(rid,graph)
        original_ids={n['id'] for n in graph['nodes']}
        bindings={n['id']:n.get('assetId') for n in graph['nodes']}
        for index,node in enumerate(graph['nodes']):
            for key in ('agent_key','agent_stage','agent_layout'):node.pop(key,None)
            node.update(x=100+index%4*340,y=100+index//4*280)
        moved=graph['nodes'][0];moved.update(x=-620,y=-520)
        artifact=run['artifacts']['analysis']
        doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{rid}:node:doc:analysis'))
        result_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{rid}:node:result:shot_1'))
        graph['nodes'].extend([
            {'id':doc_id,'type':'prompt','title':'内部分析','x':300,'y':300,'agent_run_id':rid,
             'settings':{'prompt':artifact['content']}},
            {'id':result_id,'type':'video-asset','title':'旧版重复结果','x':500,'y':300,'agent_run_id':rid,
             'assetId':run['videos']['shot_1']['asset_id']},
        ])
        graph['nodes'].append({'id':'user-note','type':'prompt','title':'手动笔记','x':-620,'y':100,'settings':{'prompt':'保留'}})
        graph['edges']=[e for e in graph['edges'] if e.get('relation')!='agent-flow']
        graph['edges'].append({'id':'user-edge','from':'user-note','to':moved['id']})
        self.host.update_creation_context(graph['id'],self.host.Project(**{k:graph[k] for k in self.host.Project.model_fields if k in graph}))
        self.service.patch(rid,status='paused')
        calls=list(self.provider_calls)
        repaired=self.service.repair_canvas(rid)
        self.assertEqual(self.provider_calls,calls)
        self.assertEqual(self.service.get(rid)['status'],'paused')
        self.assertEqual({n['id'] for n in repaired['nodes']},original_ids|{'user-note'})
        self.assertEqual({n['id']:n.get('assetId') for n in repaired['nodes'] if n['id'] in original_ids},bindings)
        self.assertEqual(self.service.video_inputs(rid,repaired),before_inputs)
        self.assertEqual((repaired['nodes'][0]['x'],repaired['nodes'][0]['y']),(-620,-520))
        self.assertEqual(repaired['nodes'][-1],graph['nodes'][-1])
        self.assertIn(graph['edges'][-1],repaired['edges'])
        self.assertEqual(self.service.repair_canvas(rid),repaired)
        for a in repaired['nodes']:
            for b in repaired['nodes']:
                if a['id']>=b['id']:continue
                self.assertFalse(a['x']<b['x']+440 and a['x']+440>b['x'] and a['y']<b['y']+340 and a['y']+340>b['y'])
        # Even a provenance edge from audio into a video must not become an
        # unsupported video-model reference or affect its approval fingerprint.
        audio=next(n for n in repaired['nodes'] if n['type']=='audio')
        video=next(n for n in repaired['nodes'] if n['type']=='video')
        repaired['edges'].insert(0,{'from':audio['id'],'to':video['id'],'relation':'agent-flow'})
        self.assertEqual(self.service.video_inputs(rid,repaired),before_inputs)
        arranged=self.service.repair_canvas(rid,force=True)
        self.assertEqual(arranged['nodes'][-1],graph['nodes'][-1])
        self.assertEqual(self.service.video_inputs(rid,arranged),before_inputs)

    def test_unstarted_shots_are_virtual_and_script_edits_still_require_review(self):
        rid=self.create();self.to_storyboard(rid)
        run=self.service.get(rid);graph=self.host.creation_context(run['project_id'])
        before=deepcopy(graph)
        projected=self.service.video_input_project(rid,graph)
        self.assertEqual(len([n for n in projected['nodes'] if n['type']=='video']),2)
        self.assertEqual(graph,before)
        self.assertEqual(self.host.creation_context(graph['id']),before)
        script=next(n for n in graph['nodes'] if n['id']==run['script_node_id'])
        script['script_data']['scenes'][0]['final_prompt']='用户手动修改后的镜头'
        self.host.update_creation_context(graph['id'],self.host.Project(**{k:graph[k] for k in self.host.Project.model_fields if k in graph}))
        with self.assertRaises(HTTPException) as caught:
            self.confirm(rid)
        self.assertEqual(caught.exception.status_code,409)
        self.assertNotIn('video',self.provider_calls)

    def test_eight_generated_shots_have_eight_video_cards_and_one_final(self):
        original=self.model_reply
        def reply(config,messages):
            response=original(config,messages)
            if 'scenes:[{id,duration' in messages[0]['content']:
                value=json.loads(response['choices'][0]['message']['content'])
                template=value['scenes'][0]
                value['scenes']=[{**deepcopy(template),'id':f's{i+1}',
                                  'line_ids':['line_1'] if i==0 else [],'description':f'第 {i+1} 镜动作'} for i in range(8)]
                response['choices'][0]['message']['content']=json.dumps(value,ensure_ascii=False)
            return response
        with patch.object(self.host,'llm_json',side_effect=reply):
            rid=self.create();self.to_storyboard(rid)
        run=self.service.get(rid)
        self.assertEqual(len([n for n in self.host.creation_context(run['project_id'])['nodes'] if n['type']=='video']),0)
        self.confirm(rid)
        # Exercise actual production and canvas writes; final local rendering is
        # covered by the two-shot integration test above.
        with patch('agent_runtime.render_timeline',side_effect=lambda *a,**k:self.add_media('video','完整视频',run['project_id'])):
            run=self.step(rid)
        self.assertEqual(run['pending_review']['gate'],'G4',run.get('error'))
        graph=self.host.creation_context(run['project_id'])
        nodes=[n for n in graph['nodes'] if n['type']=='video']
        self.assertEqual(len(nodes),8)
        self.assertEqual(self.provider_calls.count('video'),8)
        self.assertEqual({n['assetId'] for n in nodes},{v['asset_id'] for v in run['videos'].values()})
        self.assertEqual(len([n for n in graph['nodes'] if n['type']=='video-asset']),1)
        self.assertFalse(any(str(n.get('agent_key','')).startswith(('result:','doc:')) for n in graph['nodes']))

    def test_storyboard_only_does_not_generate_media(self):
        rid=self.create(preferences=Preferences(scope='storyboard'))
        self.step(rid);self.confirm(rid);run=self.step(rid)
        self.assertEqual(run['status'],'completed',run.get('error'))
        self.assertFalse(self.provider_calls)
        self.assertIn('storyboard',run['artifacts'])

    def test_confirmation_is_idempotent_and_stale_card_rejected(self):
        rid=self.create();self.step(rid)
        review=self.service.get(rid)['pending_review']
        message=Message(request_id='same-confirmation',action='confirm',review_id=review['id'])
        self.service.message(rid,message,1)
        self.service.message(rid,message,1)
        self.assertEqual(len(self.service.get(rid)['approvals']),1)
        with self.assertRaises(HTTPException) as caught:
            self.service.message(rid,Message(request_id='new-message',action='confirm',review_id=review['id']),1)
        self.assertEqual(caught.exception.status_code,409)

    def test_canvas_edit_blocks_paid_video_submission(self):
        rid=self.create();self.to_storyboard(rid);self.confirm(rid)
        run=self.service.get(rid)
        self.service.materialize_video_node(rid,run['artifacts']['storyboard']['payload']['scenes'][0],0)
        project=self.host.creation_context(run['project_id'])
        node=next(n for n in project['nodes'] if n['type']=='video')
        node['settings']['aspect_ratio']='9:16'
        self.host.update_creation_context(project['id'],self.host.Project(**{k:project[k] for k in self.host.Project.model_fields if k in project}))
        self.step(rid)
        self.assertEqual(self.service.get(rid)['status'],'blocked')
        self.assertNotIn('video',self.provider_calls)

    def test_pause_resume_and_owner_isolation(self):
        rid=self.create()
        self.service.message(rid,Message(request_id='pause',action='pause'),1)
        self.step(rid)
        self.assertEqual(self.service.get(rid)['status'],'paused')
        self.assertFalse(self.provider_calls)
        with self.assertRaises(HTTPException) as caught:
            self.service.snapshot(rid,2)
        self.assertEqual(caught.exception.status_code,404)
        self.service.message(rid,Message(request_id='resume',action='resume'),1)
        self.step(rid)
        self.assertEqual(self.service.get(rid)['pending_review']['gate'],'G1')

    def test_pause_after_first_video_resumes_without_duplicate_cards_or_requests(self):
        rid=self.create();self.to_storyboard(rid);self.confirm(rid)
        original_event=self.service.event
        def pause_after_result(run_id,kind,text,*args,**kwargs):
            result=original_event(run_id,kind,text,*args,**kwargs)
            if '第 1/2 段视频已保存' in text:
                self.service.patch(rid,status='paused',stop_requested=True,user_paused=True)
            return result
        with patch.object(self.service,'event',side_effect=pause_after_result):
            run=self.step(rid)
        self.assertEqual(run['status'],'paused',run.get('error'))
        self.assertEqual(self.provider_calls.count('video'),1)
        first=run['videos']['shot_1']['asset_id']
        graph=self.host.creation_context(run['project_id'])
        self.assertEqual(len([n for n in graph['nodes'] if n['type']=='video']),1)
        self.service.message(rid,Message(request_id='resume-partial-video',action='resume'),1)
        run=self.step(rid)
        self.assertEqual(run['pending_review']['gate'],'G4',run.get('error'))
        self.assertEqual(self.provider_calls.count('video'),2)
        self.assertEqual(run['videos']['shot_1']['asset_id'],first)
        graph=self.host.creation_context(run['project_id'])
        self.assertEqual(len([n for n in graph['nodes'] if n['type']=='video']),2)
        self.assertEqual(len([n for n in graph['nodes'] if n['type']=='video-asset']),1)

    def test_create_request_replay_and_caption_escaping(self):
        body=CreateRun(text='故事正文',request_id='stable-run-id')
        first=self.service.create(body,1)
        second=self.service.create(body,1)
        self.assertEqual(first['run']['id'],second['run']['id'])
        self.assertEqual(len(self.host.projects()),0)
        self.assertEqual(len(self.host.listing('conversation')),1)
        self.assertEqual(first['run']['creation_type'],'conversation')
        with self.assertRaises(HTTPException):
            self.service.create(body.model_copy(update={'text':'不同正文'}),1)
        self.assertNotIn('{',ass_text('{\\pos(2,3)}'))

    def test_conversation_and_canvas_have_independent_storage(self):
        canvas=self.host.create_project(self.host.Project(name='自由创作画布',nodes=[{'id':'manual','type':'prompt','title':'手动内容'}]))
        standalone=self.service.get(self.create(preferences=Preferences(canvas=True)))
        self.assertEqual(standalone['creation_type'],'conversation')
        self.assertFalse(standalone['preferences']['canvas'])
        self.assertIsNone(self.host.raw_record('project',standalone['project_id']))
        self.assertIsNotNone(self.host.raw_record('conversation',standalone['project_id']))
        self.assertEqual(self.host.active_project(canvas['id']),canvas)
        linked=self.service.get(self.create(project_id=canvas['id']))
        self.assertEqual(linked['creation_type'],'canvas')
        self.assertEqual(linked['project_id'],canvas['id'])
        self.assertEqual(len(self.host.projects()),1)
        self.assertEqual(len(self.host.listing('conversation')),1)

    def test_media_conversation_api_history_and_idempotency(self):
        browser=Browser(self.host.app)
        def request(*args):
            status,headers,raw=browser.request(*args)
            return status,headers,json.loads(raw) if raw else None
        with self.host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('conversation-test'),'admin'))
        self.assertEqual(request('POST','/api/auth/login',{'username':'admin','password':'conversation-test','remember':False})[0],200)
        payload={'title':'窗外的雨','kind':'video','prompt':'雨滴落在玻璃上','settings':{'prompt':'雨滴落在玻璃上'},'request_id':'stable-media-conversation'}
        status,_,value=request('POST','/api/conversations',payload)
        self.assertEqual(status,201,value);cid=value['id']
        self.assertEqual(request('POST','/api/conversations',payload)[2]['id'],cid)
        self.assertEqual(request('POST','/api/conversations',{**payload,'prompt':'不同内容'})[0],409)
        self.assertEqual(self.host.projects(),[])
        self.assertEqual(request('GET','/api/projects/'+cid)[0],404)
        self.host.save('task',{'id':'restored-video','project_id':cid,'status':'done','video_url':'/uploads/example.mp4','created_at':1,'params':{'prompt':payload['prompt']}})
        self.assertEqual(request('GET','/api/conversations/'+cid)[2]['tasks'][0]['id'],'restored-video')
        self.assertNotIn('nodes',request('GET','/api/conversations/'+cid)[2])
        self.assertEqual(len(request('GET','/api/conversations')[2]),1)
        reference=self.add_media('image','对话参考图','')
        context=self.host.get('conversation',cid);context['settings']['reference_asset_ids']=[reference['id']];self.host.save('conversation',context)
        usage=self.host.asset_public(reference)
        self.assertEqual(usage['usage_projects'],[])
        self.assertEqual(usage['usage_conversations'][0]['id'],cid)
        self.assertEqual(request('DELETE','/api/assets/'+reference['id'])[0],409)
        with self.host.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('conversation-member-test'), 'dingyufu'))
        self.assertEqual(request('POST','/api/auth/login',{'username':'dingyufu','password':'conversation-member-test'})[0],200)
        self.assertEqual(request('GET','/api/conversations')[2],[])
        self.assertEqual(request('GET','/api/conversations/'+cid)[0],404)

    def test_legacy_automatic_project_moves_to_conversation_with_backup(self):
        from conversation_service import migrate_agent_conversations
        rid=self.create();run=self.service.get(rid);cid=run['project_id'];graph=self.host.get('conversation',cid)
        legacy={k:v for k,v in run.items() if k!='creation_type'}
        self.host.save('agent_run',legacy);self.host.save('project',graph)
        with self.host.connect() as db:db.execute("DELETE FROM records WHERE kind='conversation' AND id=?",(cid,))
        manual=self.host.create_project(self.host.Project(name='真实画布',nodes=[{'id':'manual','type':'prompt'}]))
        migrate_agent_conversations(self.host)
        self.assertIsNone(self.host.raw_record('project',cid))
        self.assertEqual(self.host.get('conversation',cid)['nodes'],graph['nodes'])
        self.assertEqual(self.host.get('conversation',cid)['run_id'],rid)
        self.assertEqual(self.host.active_project(manual['id']),manual)
        self.assertEqual(self.service.get(rid)['creation_type'],'conversation')
        self.assertTrue(list((self.host.DATA/'backups').glob('conversation-separation-*.sqlite3')))
        migrate_agent_conversations(self.host)
        self.assertEqual(len(self.host.listing('conversation')),1)

    def test_invalid_parameter_card_is_not_consumed(self):
        rid=self.create();self.step(rid)
        review=self.service.get(rid)['pending_review']
        with self.assertRaises(HTTPException) as caught:
            self.service.message(rid,Message(request_id='invalid-params',action='confirm',review_id=review['id'],values={'target_duration':-2}),1)
        self.assertEqual(caught.exception.status_code,422)
        run=self.service.get(rid)
        self.assertEqual(run['pending_review']['id'],review['id'])
        self.assertNotIn('invalid-params',run['processed_messages'])
        self.assertFalse(self.provider_calls)

    def test_retry_regenerates_semantically_invalid_response(self):
        rid=self.create()
        def invalid(config,messages):
            response=self.model_reply(config,messages)
            if 'estimated_duration:number' in messages[0]['content']:
                response['choices'][0]['message']['content']='{"estimated_duration":0}'
            return response
        with patch.object(self.host,'llm_json',side_effect=invalid):
            self.assertEqual(self.step(rid)['status'],'blocked')
        self.service.message(rid,Message(request_id='query',action='resume'),1)
        with patch.object(self.host,'llm_json') as model:
            self.assertEqual(self.step(rid)['status'],'blocked')
            model.assert_not_called()
        self.service.message(rid,Message(request_id='retry',action='retry'),1)
        run=self.step(rid)
        self.assertEqual(run['pending_review']['gate'],'G1',run.get('error'))
        self.assertFalse(self.provider_calls)

    def test_unknown_text_request_never_replays_on_resume(self):
        rid=self.create()
        with patch.object(self.host,'llm_json',side_effect=TimeoutError('timed out')):
            run=self.step(rid)
        self.assertEqual(run['status'],'blocked')
        self.assertTrue(run['retry_warning'])
        self.service.message(rid,Message(request_id='resume-timeout',action='resume'),1)
        with patch.object(self.host,'llm_json') as model:
            self.step(rid);model.assert_not_called()
        self.service.message(rid,Message(request_id='explicit-new-request',action='retry'),1)
        self.assertEqual(self.step(rid)['pending_review']['gate'],'G1')

    def test_pause_on_review_restores_same_card(self):
        rid=self.create();self.step(rid)
        review=self.service.get(rid)['pending_review']['id']
        self.service.message(rid,Message(request_id='pause-card',action='pause'),1)
        self.service.message(rid,Message(request_id='resume-card',action='resume'),1)
        run=self.step(rid)
        self.assertEqual(run['status'],'waiting_user')
        self.assertEqual(run['pending_review']['id'],review)

    def test_parameter_reply_advances_existing_card(self):
        rid=self.create();self.step(rid)
        reply={'kind':'parameters','parameters':{'aspect_ratio':'9:16','scope':'storyboard'},'summary':'按竖屏规划分镜。'}
        def respond(config,messages):
            if '处理用户修改' in messages[0]['content']:
                return {'choices':[{'message':{'content':json.dumps(reply,ensure_ascii=False)}}]}
            return self.model_reply(config,messages)
        with patch.object(self.host,'llm_json',side_effect=respond):
            self.service.message(rid,Message(request_id='natural-parameters',text='竖屏9:16，先只做分镜'),1)
            run=self.step(rid)
        self.assertEqual(run['status'],'completed',run.get('error'))
        self.assertEqual(run['preferences']['aspect_ratio'],'9:16')
        self.assertFalse(self.provider_calls)

    def test_shot_edit_preserves_unaffected_video_and_speech(self):
        rid=self.create();self.to_storyboard(rid);self.confirm(rid);run=self.step(rid)
        before=list(self.provider_calls)
        untouched=run['videos']['shot_2']['asset_id']
        reply={'kind':'shots','changes':[{'shot_id':'shot_1','prompt':'门口的侧面远景','description':'修改构图'}],'summary':'修改第一镜构图。'}
        with patch.object(self.host,'llm_json',return_value={'choices':[{'message':{'content':json.dumps(reply,ensure_ascii=False)}}]}):
            self.service.message(rid,Message(request_id='edit-shot',text='第一镜换成侧面远景'),1)
            run=self.step(rid)
        self.assertEqual(run['pending_review']['gate'],'G3',run.get('error'))
        self.assertEqual(run['videos']['shot_2']['asset_id'],untouched)
        self.assertNotIn('shot_1',run['videos'])
        self.confirm(rid);self.step(rid)
        self.assertEqual(self.provider_calls[len(before):],['video'])
        graph=self.host.creation_context(run['project_id'])
        videos=[n for n in graph['nodes'] if n['type']=='video']
        self.assertEqual(len(videos),2)
        self.assertEqual(next(n for n in videos if n['agent_key']=='shot_2')['assetId'],untouched)
        self.assertEqual(next(n for n in videos if n['agent_key']=='shot_1')['assetId'],self.service.get(rid)['videos']['shot_1']['asset_id'])

    def test_video_recovery_queries_existing_provider_id_without_post(self):
        task={'id':'interrupted-video','status':'running','project_id':'unused','params':{'model_config_id':'builtin'},'provider_task_id':'provider-existing'}
        self.host.save('task',task)
        config={'api_key':'test-only','base_url':'https://video.invalid','api_path':'/tasks','query_path':'/tasks/{task_id}','provider':'compatible','model_id':'test','provider_id':'ark'}
        with patch.object(self.host,'provider_json',return_value={'status':'failed','error':'known failure'}) as request, patch.object(self.host.time,'sleep'):
            self.host.run_provider_task(task['id'],task['params'],config,resume=True)
        self.assertTrue(request.called)
        self.assertTrue(all(c.args[0]=='GET' for c in request.call_args_list))

    def test_video_submission_idempotency_rejects_changed_payload(self):
        rid=self.create();self.to_storyboard(rid)
        run=self.service.get(rid)
        self.service.materialize_video_node(rid,run['artifacts']['storyboard']['payload']['scenes'][0],0)
        project=self.host.creation_context(run['project_id'])
        node=next(n for n in project['nodes'] if n['type']=='video')
        request=self.host.Task(project_id=project['id'],node_id=node['id'],request_id='video-idempotency',prompt=node['settings']['prompt'],duration=4)
        # Call the real wrapper while keeping upstream workers out of this test.
        from unittest.mock import Mock
        patcher=patch.object(self.host,'_submit_video_task',wraps=self.host._submit_video_task)
        with patcher as submit, patch.object(self.host.threading,'Thread',return_value=Mock()):
            # The outer submit function is provider-substituted by setUp; use its saved original.
            first=self.video_submit_original(request)
            second=self.video_submit_original(request)
            self.assertEqual(first['id'],second['id'])
            self.assertEqual(submit.call_count,1)
            with self.assertRaises(HTTPException) as caught:
                self.video_submit_original(request.model_copy(update={'prompt':'不同镜头'}))
            self.assertEqual(caught.exception.status_code,409)

    def test_agent_http_auth_ownership_and_embedded_canvas_headers(self):
        anonymous=Browser(self.host.app)
        self.assertEqual(anonymous.request('GET','/api/agent/runs')[0],401)
        password='isolated-agent-http-test'
        with self.host.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password(password),'admin'))
            owner=db.execute('SELECT id FROM auth_users WHERE username=?',('admin',)).fetchone()[0]
        browser=Browser(self.host.app)
        self.assertEqual(browser.request('POST','/api/auth/login',{'username':'admin','password':password})[0],200)
        payload=CreateRun(text='小林回到家，母亲说：“欢迎回家。”两人相拥。片尾：一起回家。',request_id='api-run').model_dump()
        status,_,body=browser.request('POST','/api/agent/runs',payload)
        self.assertEqual(status,202,body)
        rid=json.loads(body)['run']['id']
        self.assertEqual(str(self.service.get(rid)['owner_id']),str(owner))
        other=self.service.create(CreateRun(text='另一个用户的故事',request_id='other-api-run'),'different-owner')['run']['id']
        # Administrators use the same workspace and may inspect all accounts.
        self.assertEqual(browser.request('GET','/api/agent/runs/'+other)[0],200)
        self.assertEqual(browser.request('POST','/api/agent/runs/'+other+'/canvas/layout',{})[0],200)
        self.assertEqual(browser.request('POST','/api/agent/runs/'+rid+'/canvas/layout',{})[0],200)
        self.assertEqual(browser.request('POST','/api/agent/runs',payload,origin='https://elsewhere.invalid')[0],403)
        self.assertEqual(browser.request('GET','/')[1]['x-frame-options'],'DENY')
        self.assertEqual(browser.request('GET','/?view=canvas&embedded=1')[1]['x-frame-options'],'SAMEORIGIN')


if __name__=='__main__':
    unittest.main()
