"""Audio graph/provider contracts; isolated storage, no paid API calls."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch
from fastapi import HTTPException
from PIL import Image


class VideoAudioReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix='jht-audio-reference-')
        spec=importlib.util.spec_from_file_location('video_audio_test_app',Path(__file__).resolve().parents[1]/'app.py')
        cls.host=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'JIANHUITONG_DATA_DIR':cls.directory.name}):spec.loader.exec_module(cls.host)
        audio=io.BytesIO()
        with wave.open(audio,'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b'\0\0'*48000)
        cls.audio=cls.host.store_uploaded_asset(audio.getvalue(),'reference.wav','')
        image=io.BytesIO();Image.new('RGB',(64,64),'blue').save(image,format='PNG')
        cls.image=cls.host.store_uploaded_asset(image.getvalue(),'reference.png','')
        cls.video={'id':'reference-video','kind':'video','name':'运动参考.mp4','url':'/uploads/reference.mp4','size_bytes':32}
        (cls.host.UPLOADS/'reference.mp4').write_bytes(b'\0\0\0\x18ftypisom'+b'\0'*20)
        cls.host.save('asset',cls.video)

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def graph(self,audio_only=False):
        nodes=[{'id':'audio','type':'audio','assetId':self.audio['id']},
               {'id':'image','type':'asset','assetId':self.image['id']},
               {'id':'video','type':'video','settings':{}}]
        return {'id':'project-audio','name':'reference','nodes':nodes,'edges':[{'from':source,'to':'video'} for source in (['audio'] if audio_only else ['image','audio'])]}

    def config(self,model='doubao-seedance-2-0-mini-260615',provider='ark'):
        return {'id':'model','name':'测试模型','provider_id':provider,'provider':provider,'model_id':model,'api_key':'','capabilities':{}}

    def submit(self,project,config):
        params=self.host.Task(project_id=project['id'],node_id='video',prompt='画面配合 @reference.wav')
        with patch.object(self.host,'resolve_model_config',return_value=config):
            return self.host._submit_video_task(params,project)

    def test_audio_edge_resolves_and_is_submitted_as_audio_not_image(self):
        project=self.graph();ids=self.host.video_graph_asset_ids(project,project['nodes'][-1])
        self.assertEqual(ids,[self.image['id'],self.audio['id']])
        task=self.submit(project,self.config())
        self.assertEqual(task['status'],'blocked')  # no API key: no network calls
        payload=self.host.provider_payload(task['params'],self.config())
        self.assertEqual(payload['content'][2]['type'],'audio_url')
        self.assertEqual(payload['content'][2]['role'],'reference_audio')
        self.assertTrue(payload['content'][2]['audio_url']['url'].startswith('data:audio/wav;base64,'))
        self.assertEqual(payload['content'][1]['role'],'reference_image')

    def test_seedance20_audio_requires_picture_but_25_accepts_audio_only(self):
        with self.assertRaises(HTTPException) as error:self.submit(self.graph(True),self.config())
        self.assertIn('至少一张',error.exception.detail)
        self.assertEqual(self.submit(self.graph(True),self.config('doubao-seedance-2-5'))['status'],'blocked')

    def test_unsupported_model_is_rejected_before_submission(self):
        with self.assertRaises(HTTPException) as error:self.submit(self.graph(),self.config('minimax-h3','aimarket'))
        self.assertIn('未接入音频参考',error.exception.detail)

    def test_prompt_mentions_resolve_actual_asset_ids_and_deduplicate(self):
        project=self.graph()
        project['nodes'].append({'id':'text','type':'prompt','settings':{'prompt':'使用 @配音','mentions':[{'token':'@配音','asset_id':self.audio['id']}]}})
        project['edges'].append({'from':'text','to':'video'})
        self.assertEqual(self.host.video_graph_asset_ids(project,project['nodes'][2]),[self.image['id'],self.audio['id']])
        project['edges']=[edge for edge in project['edges'] if edge['from']!='audio']
        project['nodes'][-1]['settings']['prompt']='删除了引用'
        self.assertEqual(self.host.video_graph_asset_ids(project,project['nodes'][2]),[self.image['id']])

    def video_graph(self):
        return {'id':'video-references','name':'参考视频', 'nodes':[
            {'id':'clip','type':'video-asset','assetId':self.video['id']},
            {'id':'video','type':'video','settings':{}}], 'edges':[{'from':'clip','to':'video'}]}

    def test_video_reference_is_sent_as_video_for_seedance_and_h3(self):
        for config in [self.config(),self.config('doubao-seedance-2-5'),self.config('minimax-h3','aimarket')]:
            with self.subTest(model=config['model_id']), patch('agent_media.inspect_file',return_value={'duration':5,'width':960,'height':540}):
                task=self.submit(self.video_graph(),config)
                payload=self.host.provider_payload(task['params'],config)
                ref=payload['content'][1]
                self.assertEqual(ref['type'],'video_url')
                self.assertEqual(ref['role'],'reference_video')
                self.assertTrue(ref['video_url']['url'].startswith('data:video/mp4;base64,'))

    def test_unsupported_model_and_long_or_missing_video_are_rejected(self):
        with self.assertRaises(HTTPException) as error:self.submit(self.video_graph(),self.config('old-model'))
        self.assertIn('不支持视频参考',error.exception.detail)
        with patch('agent_media.inspect_file',return_value={'duration':16,'width':960,'height':540}):
            with self.assertRaises(HTTPException):self.submit(self.video_graph(),self.config())
            self.assertEqual(self.submit(self.video_graph(),self.config('doubao-seedance-2-5'))['status'],'blocked')
        graph=self.video_graph();graph['nodes'][0]['assetId']='missing'
        with self.assertRaises(HTTPException):self.submit(graph,self.config())

    def test_video_and_audio_can_be_combined_without_picture_for_seedance20(self):
        graph=self.video_graph();graph['nodes'].append({'id':'audio','type':'audio','assetId':self.audio['id']})
        graph['edges'].append({'from':'audio','to':'video'})
        with patch('agent_media.inspect_file',return_value={'duration':5,'width':960,'height':540}):
            self.assertEqual(self.submit(graph,self.config())['status'],'blocked')

    def test_generated_node_uses_matching_completed_result_and_rejects_stale_snapshot(self):
        graph=self.video_graph();source=graph['nodes'][0]
        source.update(type='video',referenceAssetId=self.video['id'],referenceTaskId='upstream')
        source.pop('assetId')
        self.assertTrue(self.host.project_asset_references(graph,self.video['id']))
        self.host.save('asset',{**self.video,'task_id':'upstream'})
        self.host.save('task',{'id':'upstream','status':'done','project_id':graph['id'],'created_at':1,'params':{'node_id':'clip'}})
        self.assertEqual(self.host.video_graph_asset_ids(graph,graph['nodes'][1]),[self.video['id']])
        self.host.save('task',{'id':'upstream-new','status':'running','project_id':graph['id'],'created_at':2,'params':{'node_id':'clip'}})
        with self.assertRaises(HTTPException) as error:self.host.video_graph_asset_ids(graph,graph['nodes'][1])
        self.assertIn('结果已更新',error.exception.detail)


if __name__=='__main__':unittest.main()
