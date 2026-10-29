import json
import uuid
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from agent_runtime import CreateRun, Message, Preferences
from agent_studio import DocumentEdit, StudioPlan
from studio_graph import build_graph, resolve_inputs, validate_extraction, digest, binding_signature
import test_agent_runtime as runtime_fixture
from studio_fixtures import reply


class AgentStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime_fixture.AgentRuntimeTests.setUpClass.__func__(cls)
        cls.image_submit_original = staticmethod(cls.host.create_image_generation)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        runtime_fixture.AgentRuntimeTests.setUp(self)
        patch.object(self.host, 'model_for_generation', side_effect=lambda config: {**config, 'api_key': 'test-only'}).start()
    add_media = runtime_fixture.AgentRuntimeTests.add_media
    image = runtime_fixture.AgentRuntimeTests.image
    audio = runtime_fixture.AgentRuntimeTests.audio
    video = runtime_fixture.AgentRuntimeTests.video
    model_reply = staticmethod(reply)

    def create(self, text='从一句话制作一部 2 集短剧', **extra):
        return self.service.create(CreateRun(text=text, request_id=str(uuid.uuid4()), **extra), 1)['run']['id']

    def step(self, rid):
        self.service.work(rid)
        run = self.service.get(rid)
        self.assertNotEqual(run['status'], 'blocked', run.get('error'))
        return run

    def message(self, rid, **fields):
        return self.service.message(rid, Message(request_id=str(uuid.uuid4()), **fields), 1)

    def image_variant(self, graph):
        """A user-created costume reference exercises native image dependencies."""
        root = next(n for n in graph['nodes'] if n.get('mujian', {}).get('assetType') == 'character')
        node = deepcopy(root)
        node.update(id='test-character-variant', title='小林雨衣参考')
        node['mujian'].update(key='asset:test-variant', definitionId='test-variant',
            inputs=[{'nodeId': root['id'], 'kind': 'image', 'role': '人物身份', 'slot': ''}])
        node['image_settings'].update(prompt='@小林 保持人物身份，穿蓝色雨衣。',
            mentions=[{'token': '@小林', 'node_id': root['id'], 'asset_id': '', 'name': root['title'], 'kind': 'image'}])
        graph['nodes'].append(node)
        graph['edges'].append({'from': root['id'], 'to': node['id'], 'relation': 'reference'})
        return node

    def test_chat_never_starts_media_or_script_and_can_change_intent(self):
        rid = self.create('你好，解释一下电影镜头的作用')
        run = self.step(rid)
        self.assertEqual(run['status'], 'completed')
        self.assertFalse(run['studio']['documents'])
        self.assertFalse(self.host.listing('project'))
        self.assertFalse(self.provider_calls)
        self.message(rid, text='生成图片：窗边的橘猫')
        self.step(rid)
        self.assertEqual(self.provider_calls, ['image'])
        self.assertFalse(self.host.listing('project'))

    def test_initial_conversation_title_reuses_route_and_ignores_later_titles(self):
        rid = self.create('你好，解释一下电影镜头的作用')
        contexts = []
        def answer(config, messages):
            contexts.append(json.loads(messages[-1]['content']))
            return {'choices': [{'message': {'content': json.dumps({
                'intent': 'chat', 'reply': '镜头负责叙事。', 'conversation_title': '电影镜头的作用',
            }, ensure_ascii=False)}}]}
        with patch.object(self.host, 'llm_json', side_effect=answer) as provider:
            run = self.step(rid)
            self.assertEqual(provider.call_count, 1)
            self.assertTrue(contexts[0]['initial_conversation'])
            self.assertEqual(self.host.get('conversation', run['project_id'])['title'], '电影镜头的作用')
            self.message(rid, text='再解释一下灯光')
            self.step(rid)
            self.assertFalse(contexts[-1]['initial_conversation'])
            self.assertEqual(self.host.get('conversation', run['project_id'])['title'], '电影镜头的作用')

    def test_manual_rename_while_first_route_is_running_wins(self):
        rid = self.create('你好，解释一下电影镜头的作用')
        cid = self.service.get(rid)['project_id']
        def answer(config, messages):
            value = self.host.get('conversation', cid)
            self.host.save('conversation', {**value, 'name': '我的学习笔记', 'title': '我的学习笔记', 'title_source': 'manual'})
            return {'choices': [{'message': {'content': json.dumps({
                'intent': 'chat', 'reply': '镜头负责叙事。', 'conversation_title': '电影镜头的作用',
            }, ensure_ascii=False)}}]}
        with patch.object(self.host, 'llm_json', side_effect=answer):
            self.step(rid)
        self.assertEqual(self.host.get('conversation', cid)['title'], '我的学习笔记')
        self.assertEqual(StudioPlan(intent='chat', reply='正常回复', conversation_title=None).conversation_title, '')

    def test_canvas_questions_receive_live_graph_selection_and_skills(self):
        graph = self.host.create_project(self.host.Project(name='测试画布', nodes=[
            {'id': 'idea', 'type': 'prompt', 'title': '故事想法', 'text': '雨后回家', 'x': 100, 'y': 200},
        ], edges=[]))
        captured = []

        def aware_reply(config, messages):
            context = json.loads(messages[-1]['content'])
            captured.append(context)
            self.assertIn('不能声称看到了', messages[0]['content'])
            return {'choices': [{'message': {'content': json.dumps({'intent': 'chat', 'reply': '当前画布是' + context['canvas']['project']['name']}, ensure_ascii=False)}}]}

        rid = self.create('我在哪个项目', project_id=graph['id'], canvas_selection={'selected_node_ids': ['idea', 'foreign-node']})
        with patch.object(self.host, 'llm_json', side_effect=aware_reply):
            self.step(rid)
            first = captured[-1]
            self.assertEqual(first['canvas']['project']['name'], '测试画布')
            self.assertEqual(first['canvas']['selection']['selected_node_ids'], ['idea'])
            self.assertEqual(first['canvas']['nodes'][0]['text'], '雨后回家')
            self.assertIn('canvas-context', {s['id'] for s in first['skills']})
            self.assertIn('studio-production', {s['id'] for s in first['skills']})
            self.host.save('project', {**graph, 'name': '已改名的画布', 'revision': graph['revision'] + 1,
                                       'nodes': [*graph['nodes'], {'id': 'new', 'type': 'prompt', 'title': '新增节点', 'x': 500, 'y': 200}]})
            self.message(rid, text='现在是什么画布', values={'canvas_selection': {'selected_node_ids': ['new']}})
            self.step(rid)
            self.assertEqual(captured[-1]['canvas']['project']['name'], '已改名的画布')
            self.assertEqual(captured[-1]['canvas']['summary']['node_count'], 2)
            self.assertEqual(captured[-1]['canvas']['nodes'][0]['id'], 'new')
        self.assertFalse(self.provider_calls)
        self.assertFalse(self.service.get(rid)['studio']['documents'])
        bad_id = str(uuid.uuid4())
        with self.assertRaises(HTTPException):
            self.service.message(rid, Message(request_id=bad_id, text='问答', values={'canvas_selection': {'selected_node_ids': 'bad'}}), 1)
        self.assertNotIn(bad_id, self.service.get(rid)['processed_messages'])

    def test_direct_video_reuses_host_service(self):
        rid = self.create('生成视频：窗边的橘猫转头')
        self.step(rid)
        self.assertEqual(self.provider_calls, ['video'])
        self.assertFalse(self.service.get(rid)['studio']['documents'])
        media = [e for e in self.host.listing('agent_event') if e['run_id'] == rid and e['kind'] == 'media']
        self.assertEqual(media[0]['asset']['kind'], 'video')

    def test_chat_repairs_received_non_json_and_non_object_replies_once(self):
        for text in ('下午好！', '{"intent":"chat","reply":"下午好', '', '[]', 'null'):
            with self.subTest(text=text):
                rid = self.create('下午好')
                good = {'choices': [{'message': {'content': '{"intent":"chat","reply":"下午好！"}'}}]}
                with patch.object(self.host, 'llm_json', side_effect=[{'choices': [{'message': {'content': text}}]}, good]) as calls:
                    run = self.step(rid)
                self.assertEqual(calls.call_count, 2)
                repair = json.loads(calls.call_args.args[1][-1]['content'])['repair']
                self.assertTrue(repair['error'])
                self.assertEqual(run['status'], 'completed')
                self.assertFalse(run['studio']['documents'])
                self.assertFalse(self.provider_calls)
                events = self.service.snapshot(rid, 1)['events']
                self.assertEqual([e['text'] for e in events if e['kind'] == 'assistant'], ['下午好！'])
                self.assertFalse(any(e['kind'] == 'issue' for e in events))

    def test_repeated_non_json_stops_and_explicit_retry_recovers(self):
        rid = self.create('只写故事设定'); initial = self.step(rid)
        self.message(rid, text='下午好')
        bad = {'choices': [{'message': {'content': '普通文字回复'}}]}
        with patch.object(self.host, 'llm_json', return_value=bad) as calls:
            self.service.work(rid)
            self.assertEqual(calls.call_count, 2)
            blocked = self.service.get(rid)
            self.assertEqual(blocked['status'], 'blocked')
            self.assertEqual(blocked['studio']['documents'], initial['studio']['documents'])
            self.message(rid, action='resume'); self.service.work(rid)
            self.assertEqual(calls.call_count, 2)
        operations = [op for op in self.host.listing('agent_operation') if op['run_id'] == rid and op['status'] == 'invalid']
        self.assertEqual(len(operations), 2)
        self.assertNotIn('普通文字回复', json.dumps(self.service.snapshot(rid, 1), ensure_ascii=False))
        self.message(rid, action='retry')
        self.assertEqual(self.step(rid)['studio']['documents'], initial['studio']['documents'])

    def test_non_json_repair_timeout_never_replays_on_resume(self):
        for responses in ([RuntimeError('模型接口读取超时')],
                          [{'choices': [{'message': {'content': '下午好！'}}]}, RuntimeError('模型接口读取超时')]):
            with self.subTest(attempts=len(responses)):
                rid = self.create('下午好')
                with patch.object(self.host, 'llm_json', side_effect=responses) as calls:
                    self.service.work(rid)
                    run = self.service.get(rid)
                    self.assertEqual(run['status'], 'blocked')
                    self.assertTrue(run['retry_warning'])
                    self.assertEqual(calls.call_count, len(responses))
                    self.message(rid, action='resume'); self.service.work(rid)
                    self.assertEqual(calls.call_count, len(responses))

    def test_chat_ignores_unused_script_fields_without_another_model_request(self):
        for intent in ('chat', 'clarify'):
            with self.subTest(intent=intent):
                rid = self.create('李白是谁？')
                answer = '李白是唐代诗人，字太白，号青莲居士，被后人称为“诗仙”。'
                value = {'intent': intent, 'reply': answer, 'action': '', 'episode': None,
                         'target': None, 'title': None, 'brief_patch': None, 'instruction': None,
                         'cascade': None, 'prompt': None, 'assumptions': None, 'media_settings': None}
                with patch.object(self.host, 'llm_json', return_value={'choices': [{'message': {'content': json.dumps(value)}}]}) as calls:
                    run = self.step(rid)
                calls.assert_called_once()
                self.assertEqual(run['status'], 'completed')
                self.assertFalse(run['studio']['documents'])
                self.assertFalse(run['studio']['brief'])
                self.assertFalse(self.provider_calls)
                events = self.service.snapshot(rid, 1)['events']
                self.assertEqual([event['text'] for event in events if event['kind'] == 'assistant'], [answer])
                self.assertFalse(any(event['kind'] in ('issue', 'progress') for event in events))

    def test_unused_fields_do_not_break_media_or_production_but_real_inputs_are_checked(self):
        for intent in ('image', 'video', 'produce'):
            with self.subTest(intent=intent):
                plan = StudioPlan(intent=intent, reply='好的，我来帮您准备。', action='', episode=None, target=None, cascade=None)
                self.assertEqual(plan.intent, intent)
        for fields in ({'action': ''}, {'action': 'episode', 'episode': None},
                       {'action': 'episode', 'episode': 61}, {'action': 'revise', 'target': None}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                StudioPlan(intent='script', **fields)
        with self.assertRaises(ValidationError):
            StudioPlan(intent='image', prompt=None)

    def test_invalid_route_keeps_diagnostics_internal_and_preserves_existing_documents(self):
        rid = self.create('只写故事设定'); initial = self.step(rid)
        self.message(rid, text='继续写剧本')
        malformed = {'choices': [{'message': {'content': json.dumps({'intent': 'script', 'action': ''})}}]}
        with patch.object(self.host, 'llm_json', return_value=malformed) as calls:
            self.service.work(rid)
        self.assertEqual(calls.call_count, 2)
        repair = json.loads(calls.call_args.args[1][-1]['content'])['repair']
        self.assertIn('validation error', repair['error'])
        snapshot = self.service.snapshot(rid, 1)
        self.assertEqual(snapshot['run']['status'], 'blocked')
        self.assertEqual(snapshot['run']['studio']['documents'], initial['studio']['documents'])
        self.assertIn('请您重试', snapshot['run']['error'])
        self.assertNotRegex(json.dumps(snapshot, ensure_ascii=False), r'validation error|errors\.pydantic\.dev|input_value=')
        internal = [op for op in self.host.listing('agent_operation') if op['run_id'] == rid and op['status'] == 'invalid']
        self.assertEqual(len(internal), 2)
        self.assertTrue(all('validation error' in op['error'] for op in internal))
        self.message(rid, action='retry'); finished = self.step(rid)
        self.assertEqual(finished['studio']['documents']['bible'], initial['studio']['documents']['bible'])
        self.assertEqual(finished['studio']['documents']['episode-2']['version'], 1)

    def test_saved_diagnostics_are_sanitized_without_filtering_user_or_assistant_english(self):
        rid = self.create('解释 validation errors for StudioPlan 的含义'); self.step(rid)
        detail = '2 validation errors for StudioPlan\naction\nInput should be valid [type=literal_error, input_value=""]\nhttps://errors.pydantic.dev/2.10/v/literal_error'
        self.service.patch(rid, status='blocked', error=detail)
        event = self.service.event(rid, 'progress', '正在核对并补齐：' + detail)
        self.service.event(rid, 'issue', detail)
        answer = 'validation errors 表示输入没有通过检查，您可以查看提示定位具体字段。'
        self.service.event(rid, 'assistant', answer)
        for after in (0, event['seq'] - 1):
            snapshot = self.service.snapshot(rid, 1, after)
            self.assertIn('请您重试', snapshot['run']['error'])
            system_text = '\n'.join(e['text'] for e in snapshot['events'] if e['kind'] in ('progress', 'issue', 'step'))
            self.assertNotRegex(system_text, r'validation error|errors\.pydantic\.dev|input_value=')
            self.assertEqual(snapshot['events'][-1]['text'], answer)
        self.assertEqual(self.service.snapshot(rid, 1)['events'][0]['text'], '解释 validation errors for StudioPlan 的含义')
        self.assertEqual(self.service.get(rid)['error'], detail)
        self.assertEqual(self.host.get('agent_event', event['id'])['text'], '正在核对并补齐：' + detail)

    def test_script_scope_does_not_create_canvas_or_media(self):
        rid = self.create('只写故事设定')
        run = self.step(rid)
        self.assertEqual(list(run['studio']['documents']), ['bible'])
        self.assertIsNone(run['studio']['canvas_project_id'])
        self.assertFalse(self.provider_calls)

    def test_production_graph_has_real_references_and_episode_groups(self):
        rid = self.create()
        run = self.step(rid)
        self.assertEqual(set(run['studio']['documents']), {'bible', 'outline', 'episode-1', 'episode-2'})
        graph = self.host.active_project(run['studio']['canvas_project_id'])
        self.assertEqual(self.service.snapshot(rid, 1)['canvas_project_revision'], graph['revision'])
        self.host.save('project', {**graph, 'revision': graph['revision'] + 1})
        self.assertEqual(self.service.snapshot(rid, 1)['canvas_project_revision'], graph['revision'] + 1)
        self.assertEqual({g['title'] for g in graph['groups']}, {'人物 · 小林', '道具参考', '场景 · 小镇车站', '第 1 集 · 视频片段', '第 2 集 · 视频片段'})
        shots = [n for n in graph['nodes'] if n.get('mujian', {}).get('role') == 'shot']
        self.assertEqual(len(shots), 4)
        for shot in shots:
            self.assertTrue(shot['mujian']['inputs'])
            self.assertTrue(all(any(e['from'] == r['nodeId'] and e['to'] == shot['id'] and e['relation'] == 'reference' for e in graph['edges']) for r in shot['mujian']['inputs']))
            self.assertTrue(any(shot['id'] in group['node_ids'] for group in graph['groups']))
            self.assertTrue(all(e.get('hidden') for e in graph['edges'] if e['to'] == shot['id']))
            self.assertTrue(shot['settings']['mentions'])
            self.assertIn('@', shot['settings']['prompt'])
        grouped = [nid for group in graph['groups'] for nid in group['node_ids']]
        self.assertEqual(len(grouped), len(set(grouped)))
        for group in graph['groups']:
            if '视频片段' in group['title']:
                self.assertTrue(all(next(n for n in graph['nodes'] if n['id'] == nid)['type'] == 'video' for nid in group['node_ids']))
        self.assertFalse(any(n.get('assetId') for n in graph['nodes']))
        self.assertFalse(self.provider_calls)
        with self.assertRaises(HTTPException) as error: resolve_inputs(self.host, graph, shots[0])
        self.assertIn('请先生成', str(error.exception.detail))
        # A complete snapshot survives a fresh service instance without submission.
        from agent_runtime import AgentService
        restored = AgentService(self.service.h).snapshot(rid, 1)
        self.assertEqual(restored['run']['studio'], run['studio'])

    def test_revision_keeps_history_marks_dependants_and_preserves_user_canvas(self):
        rid = self.create(); run = self.step(rid)
        pid = run['studio']['canvas_project_id']; graph = self.host.active_project(pid)
        first = next(n for n in graph['nodes'] if n.get('mujian', {}).get('role') == 'shot')
        first['x'] = 911; first['settings']['prompt'] = '用户自定构图'
        graph['nodes'].append({'id': 'user-node', 'type': 'prompt', 'title': '手动笔记', 'x': 30, 'y': 40, 'settings': {'prompt': '保留'}})
        self.host.update_project(pid, self.host.Project(**graph))
        doc = run['studio']['documents']['episode-1']
        result = self.service.studio_edit(rid, 'episode-1', DocumentEdit(revision=run['revision'], content=doc['content'].replace('灰色', '蓝色') + '\n△小林停下脚步。'), 1)
        self.assertEqual(result['run']['studio']['documents']['episode-2']['status'], 'stale')
        self.assertEqual(result['run']['studio']['documents']['episode-1']['version'], 2)
        self.assertEqual(len([a for a in self.host.listing('agent_artifact') if a['name'] == 'studio:episode-1']), 2)
        latest = self.host.active_project(pid)
        self.assertTrue(next(n for n in latest['nodes'] if n['id'] == first['id'])['mujian']['needsReview'])
        # Rebuild with unchanged definitions preserves manual content and layout.
        merged = build_graph(run, latest)
        self.assertTrue(any(n['id'] == 'user-node' for n in merged['nodes']))
        node = next(n for n in merged['nodes'] if n['id'] == first['id'])
        self.assertEqual(node['x'], 911); self.assertEqual(node['settings']['prompt'], '用户自定构图')
        with self.assertRaises(HTTPException): self.service.studio_edit(rid, 'bible', DocumentEdit(revision=1, content=doc['content']), 1)

    def test_idempotency_and_account_ownership(self):
        body = CreateRun(text='你好', request_id='studio-repeated')
        first = self.service.create(body, 1)
        second = self.service.create(body, 1)
        self.assertEqual(first['run']['id'], second['run']['id'])
        with self.assertRaises(HTTPException): self.service.create(body.model_copy(update={'text': '不同内容'}), 1)
        with self.assertRaises(HTTPException): self.service.get(first['run']['id'], 2)

    def test_explicit_make_film_renders_episode_then_full_movie(self):
        rid = self.create(); run = self.step(rid)
        self.assertFalse(self.provider_calls)
        self.message(rid, action='studio_generate')
        run = self.step(rid)
        graph = self.host.active_project(run['studio']['canvas_project_id'])
        final = next(n for n in graph['nodes'] if n.get('mujian', {}).get('role') == 'final')
        from agent_media import inspect_asset
        actual = inspect_asset(self.host, final['assetId'])
        self.assertAlmostEqual(actual['duration'], 16, delta=.5)
        self.assertEqual(self.provider_calls.count('video'), 4)
        self.assertEqual(self.provider_calls.count('image'), 3)
        self.assertTrue(all(n.get('assetId') for n in graph['nodes'] if n['type'] != 'prompt'))
        self.assertFalse(self.host.get('asset', final['assetId']).get('in_library'))
        # Repeating an explicit production action reuses all valid completed media.
        self.message(rid, action='studio_generate'); self.step(rid)
        self.assertEqual(self.provider_calls.count('video'), 4)

    def test_pause_resume_keeps_completed_documents_and_provider_receipts(self):
        rid = self.create()
        put = self.service.studio_put_document
        def pause_after_first(*args, **kwargs):
            put(*args, **kwargs)
            if args[1] == 'bible': self.message(rid, action='pause')
        with patch.object(self.service, 'studio_put_document', side_effect=pause_after_first):
            self.service.work(rid)
        run = self.service.get(rid)
        self.assertEqual(run['status'], 'paused')
        self.assertIn('bible', run['studio']['documents'])
        self.message(rid, action='resume'); self.step(rid)
        bible_calls = [c for c in self.host.llm_json.call_args_list if '只输出故事设定' in c.args[1][0]['content']]
        self.assertEqual(len(bible_calls), 1)
        before = self.host.llm_json.call_count
        self.service.snapshot(rid, 1); self.service.snapshot(rid, 1)
        self.assertEqual(before, self.host.llm_json.call_count)

    def test_invalid_preferences_and_scope_do_not_consume_request_id(self):
        rid = self.create('你好'); self.step(rid)
        bad = Message(request_id='invalid-preference', action='preferences', values={'studio_video_mode': 'invalid'})
        with self.assertRaises(HTTPException): self.service.message(rid, bad, 1)
        self.assertNotIn(bad.request_id, self.service.get(rid)['processed_messages'])
        self.service.message(rid, bad.model_copy(update={'values': {'studio_video_mode': 'first-last'}}), 1)
        self.assertEqual(self.service.get(rid)['preferences']['studio_video_mode'], 'first-last')
        self.message(rid, text='生成图片', values={'read_only': True})
        self.step(rid)
        self.assertFalse(self.provider_calls)

    def test_missing_reference_is_rejected_by_native_image_and_video_apis(self):
        rid = self.create(); run = self.step(rid)
        graph = self.host.active_project(run['studio']['canvas_project_id'])
        first = self.image_variant(graph)
        graph = self.host.update_project(graph['id'], self.host.Project(**graph))
        video = next(n for n in graph['nodes'] if n.get('mujian', {}).get('role') == 'shot')
        with self.assertRaises(HTTPException) as image_error:
            self.image_submit_original(self.host.ImageGenerationRequest(project_id=graph['id'], node_id=first['id'], prompt='不能跳过前置图片'))
        self.assertIn('请先生成', image_error.exception.detail)
        with self.assertRaises(HTTPException) as video_error:
            self.video_submit_original(self.host.Task(project_id=graph['id'], node_id=video['id'], prompt='不能跳过人物与场景参考'))
        self.assertIn('请先生成', video_error.exception.detail)
        self.assertFalse(self.provider_calls)

    def test_native_node_mentions_compile_without_changing_draft_or_replaying_job(self):
        rid = self.create(); run = self.step(rid)
        graph = self.host.active_project(run['studio']['canvas_project_id'])
        first = self.image_variant(graph)
        for ref in first['mujian']['inputs']:
            source = next(n for n in graph['nodes'] if n['id'] == ref['nodeId'])
            asset = self.add_media('image', source['title'], graph['id'])
            source.update(assetId=asset['id'], assetUrl=asset['url'])
        graph = self.host.update_project(graph['id'], self.host.Project(**graph))
        ids = resolve_inputs(self.host, graph, first)
        request = self.host.ImageGenerationRequest(project_id=graph['id'], node_id=first['id'], request_id='node-mentions-image', name=first['title'], prompt=first['image_settings']['prompt'], reference_asset_ids=ids)
        with patch.object(self.host, 'start_generation_worker') as worker:
            task = self.image_submit_original(request)
            replay = self.image_submit_original(request)
        self.assertEqual(task['id'], replay['id']); worker.assert_called_once()
        stored = self.host.get('image_task', task['id'])
        self.assertEqual(stored['prompt'], request.prompt)
        self.assertNotIn('@', stored['effective_prompt'])
        self.assertIn('图片1', stored['effective_prompt'])
        for source in graph['nodes']:
            if source.get('mujian', {}).get('role') == 'reference' and not source.get('assetId'):
                asset = self.add_media('image', source['title'], graph['id'])
                source.update(assetId=asset['id'], assetUrl=asset['url'])
        graph = self.host.update_project(graph['id'], self.host.Project(**graph))
        video = next(n for n in graph['nodes'] if n.get('mujian', {}).get('role') == 'shot')
        config = self.host.internal_registry_model('video', video['settings']['model_config_id'])
        video_ids = resolve_inputs(self.host, graph, video)
        self.assertEqual(len(video_ids), 3)
        request = self.host.Task(project_id=graph['id'], node_id=video['id'], request_id='node-mentions-video', prompt=video['settings']['prompt'], mode='image-to-video', duration=video['settings']['duration'], aspect_ratio=video['settings']['aspect_ratio'], resolution=video['settings']['resolution'], model_config_id=config['id'], asset_ids=video_ids)
        with patch.object(self.host, 'start_generation_worker') as worker:
            task = self.video_submit_original(request)
            replay = self.video_submit_original(request)
        self.assertEqual(task['id'], replay['id']); worker.assert_called_once()
        self.assertEqual(task['params']['prompt'], request.prompt)
        self.assertNotIn('@', task['params']['effective_prompt'])

    def test_semantic_failure_can_retry_only_unfinished_stage(self):
        rid = self.create()
        def malformed(config, messages):
            response = reply(config, messages)
            if '分集大纲，每集独立标题' in messages[0]['content']:
                value = json.loads(response['choices'][0]['message']['content'])
                value['content'] = '# 分集大纲\n## 第1集\n' + '缺少第二集的测试大纲。' * 15
                response['choices'][0]['message']['content'] = json.dumps(value)
            return response
        with patch.object(self.host, 'llm_json', side_effect=malformed): self.service.work(rid)
        self.assertEqual(self.service.get(rid)['status'], 'blocked')
        self.message(rid, action='retry'); run = self.step(rid)
        self.assertEqual(run['studio']['documents']['bible']['version'], 1)
        self.assertEqual(run['studio']['documents']['outline']['version'], 1)

    def test_legacy_oversized_style_is_repaired_without_rewriting_finished_script(self):
        rid = self.create('写剧本：创作一个 2 集故事'); initial = self.step(rid)
        self.message(rid, action='studio_canvas')
        self.service.studio_route(rid)
        job = self.service.get(rid)['studio_job']
        key = f'studio:{digest(job["id"])[:24]}:style'
        # The old version cached the provider response as successful before
        # checking its length, so both resume and retry reused the bad result.
        long_style = ('写实电影质感，自然光，保持角色身份、时代和道具一致。' * 100)[:1735]
        result = {'style_bible': long_style, 'assumptions': []}
        oid = f'{rid}:op:{key}:0'
        self.host.save('agent_operation', {'id': oid, 'run_id': rid, 'key': key, 'status': 'succeeded', 'result': result})
        self.service.patch(rid, status='blocked', error='美术方向需为 1200 字内的文字')
        self.message(rid, action='retry')
        with patch.object(self.host, 'llm_json', side_effect=reply) as requests:
            finished = self.step(rid)
        self.assertEqual(finished['studio']['documents'], initial['studio']['documents'])
        self.assertTrue(finished['studio']['canvas_project_id'])
        style_calls = [call for call in requests.call_args_list if '统一美术方向' in call.args[1][0]['content']]
        self.assertEqual(len(style_calls), 1)
        context = json.loads(style_calls[0].args[1][-1]['content'])
        self.assertEqual(context['repair']['previous'], result)
        self.assertIn('1735', context['repair']['error'])
        self.assertLessEqual(len(finished['studio']['style_bible']), 1200)
        self.assertNotEqual(finished['studio']['style_bible'], long_style[:1200])
        self.assertEqual(self.host.get('agent_operation', oid)['result'], result)
        self.assertFalse(any('最新消息' in call.args[1][0]['content'] or 'content,summary' in call.args[1][0]['content'] for call in requests.call_args_list))
        graph = self.host.active_project(finished['studio']['canvas_project_id'])
        self.assertTrue(all(len(node.get('image_settings', node.get('settings', {})).get('prompt', '')) <= 4000
                            for node in graph['nodes'] if node['mujian']['role'] != 'script'))

    def test_invalid_style_blocks_with_correct_status_then_retries_only_unfinished_steps(self):
        rid = self.create()
        def invalid_style(config, messages):
            if '统一美术方向' in messages[0]['content']:
                context = json.loads(messages[-1]['content'])
                value = {'style_bible': None if 'repair' in context else '风格' * 900}
                return {'choices': [{'message': {'content': json.dumps(value)}}]}
            return reply(config, messages)
        with patch.object(self.host, 'llm_json', side_effect=invalid_style) as requests:
            self.service.work(rid)
        self.assertEqual(sum('统一美术方向' in call.args[1][0]['content'] for call in requests.call_args_list), 2)
        blocked = self.service.get(rid)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('美术方向还没整理好', blocked['error'])
        self.assertFalse(blocked['studio'].get('style_bible'))
        self.assertFalse(blocked['studio']['canvas_project_id'])
        operations = [op for op in self.host.listing('agent_operation') if op['run_id'] == rid and ':style' in op['key']]
        self.assertTrue(all(op['status'] == 'invalid' for op in operations))
        events = self.service.snapshot(rid, 1)['events']
        style_events = [event for event in events if event['kind'] == 'step' and ':style' in event['step_id']]
        self.assertFalse(any(event['status'] == 'done' for event in style_events))
        self.assertEqual(style_events[-1]['status'], 'failed')
        self.assertNotIn('style_bible', '\n'.join(event['text'] for event in events))
        documents = deepcopy(blocked['studio']['documents'])
        self.message(rid, action='retry')
        with patch.object(self.host, 'llm_json', side_effect=reply) as requests:
            finished = self.step(rid)
        self.assertEqual(finished['studio']['documents'], documents)
        self.assertTrue(finished['studio']['canvas_project_id'])
        self.assertEqual(sum('统一美术方向' in call.args[1][0]['content'] for call in requests.call_args_list), 1)
        self.assertFalse(self.provider_calls)

    def test_style_repair_timeout_is_not_replayed_by_resume(self):
        rid = self.create()
        def timeout_repair(config, messages):
            if '统一美术方向' in messages[0]['content']:
                if 'repair' in json.loads(messages[-1]['content']):
                    raise RuntimeError('模型接口读取超时（900秒），模型可能仍在处理，请稍后重试。')
                return {'choices': [{'message': {'content': json.dumps({'style_bible': '写实风格' * 400})}}]}
            return reply(config, messages)
        with patch.object(self.host, 'llm_json', side_effect=timeout_repair) as requests:
            self.service.work(rid)
            blocked = self.service.get(rid)
            self.assertEqual(blocked['status'], 'blocked')
            self.assertTrue(blocked['retry_warning'])
            calls = requests.call_count
            self.message(rid, action='resume'); self.service.work(rid)
            self.assertEqual(requests.call_count, calls)
        documents = deepcopy(blocked['studio']['documents'])
        self.message(rid, action='retry')
        with patch.object(self.host, 'llm_json', side_effect=reply) as requests:
            finished = self.step(rid)
        self.assertEqual(finished['studio']['documents'], documents)
        self.assertEqual(sum('统一美术方向' in call.args[1][0]['content'] for call in requests.call_args_list), 1)
        self.assertTrue(finished['studio']['canvas_project_id'])

    def test_extraction_checks_every_scene_and_original_dialogue(self):
        rid = self.create(); run = self.step(rid)
        doc = run['studio']['documents']['episode-1']['content']
        pack = run['studio']['extractions']['1']
        invalid = {'assets': [], 'shots': deepcopy(pack['shots'])}
        invalid['shots'][0]['dialogue'] = '改变了原文的对白'
        with self.assertRaises(HTTPException) as error:
            validate_extraction(invalid, doc, 1, run['studio']['catalog'])
        self.assertIn('对白', error.exception.detail)
        invalid['shots'] = deepcopy(pack['shots'][:1])
        with self.assertRaises(HTTPException) as error:
            validate_extraction(invalid, doc, 1, run['studio']['catalog'])
        self.assertIn('全部原文场次', error.exception.detail)

    def test_render_job_binds_real_video_and_preserves_concurrent_edits(self):
        from studio_render import RenderRequest
        rid = self.create(); run = self.step(rid); pid = run['studio']['canvas_project_id']
        graph = self.host.active_project(pid)
        timeline = next(n for n in graph['nodes'] if n.get('mujian', {}).get('role') == 'edit')
        for clip in timeline['clips']:
            node = next(n for n in graph['nodes'] if n['id'] == clip['nodeId'])
            asset = self.add_media('video', '真实测试片段', pid); node.update(assetId=asset['id'], assetUrl=asset['url'])
        graph = self.host.update_project(pid, self.host.Project(**graph))
        service = self.host.app.state.studio_render
        body = RenderRequest(project_id=pid, node_id=timeline['id'], revision=graph['revision'], request_id='render-real')
        with patch('studio_render.threading.Thread.start'): job = service.create(body, 1)
        service.work(job['id'])
        job = self.host.get('studio_render', job['id'])
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertTrue(job['bound'])
        self.assertEqual(service.create(body, 1)['id'], job['id'])
        graph = self.host.active_project(pid)
        changed = body.model_copy(update={'revision': graph['revision'], 'request_id': 'render-conflict'})
        with patch('studio_render.threading.Thread.start'): other = service.create(changed, 1)
        def render_with_edit(*args, **kwargs):
            current = self.host.active_project(pid)
            target = next(n for n in current['nodes'] if n['id'] == timeline['id'])
            target['clips'].reverse()
            self.host.update_project(pid, self.host.Project(**current))
            return self.add_media('video', '保留冲突成品', pid)
        with patch('studio_render.render_node', side_effect=render_with_edit): service.work(other['id'])
        result = self.host.get('studio_render', other['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertFalse(result['bound'])
        self.assertTrue(self.host.require_live_asset(result['asset']['id']))

    def test_added_references_participate_in_requests_and_binding_conflicts(self):
        rid = self.create(); run = self.step(rid)
        graph = self.host.active_project(run['studio']['canvas_project_id'])
        node = self.image_variant(graph)
        expected = []
        for ref in node['mujian']['inputs']:
            source = next(n for n in graph['nodes'] if n['id'] == ref['nodeId'])
            asset = self.add_media('image', source['title'], graph['id'])
            source.update(assetId=asset['id']); expected.append(asset['id'])
        extra = self.add_media('image', '手工添加的参考图', graph['id'])
        graph['nodes'].append({'id': 'manual-ref', 'type': 'asset', 'title': '手工参考', 'assetId': extra['id']})
        graph['edges'].append({'from': 'manual-ref', 'to': node['id'], 'relation': 'reference'})
        manual = self.add_media('image', '参数面板参考图', graph['id'])
        node['image_settings']['reference_asset_ids'] = [manual['id']]
        self.assertEqual(resolve_inputs(self.host, graph, node), expected + [extra['id'], manual['id']])
        original = binding_signature(graph, node)
        graph['nodes'][-1]['assetId'] = manual['id']
        self.assertNotEqual(binding_signature(graph, node), original)
        self.host.save('asset', {**manual, 'deleted_at': 1})
        with self.assertRaises(HTTPException): resolve_inputs(self.host, graph, node)

    def test_reused_asset_descriptions_are_validated(self):
        rid = self.create(); run = self.step(rid)
        studio = run['studio']; source = studio['catalog'][0]
        raw = {**source, 'reuseId': source['key'], 'sourceScene': '1-1', 'sourceLine': 6}
        for field, invalid in [('facts', {}), ('design', []), ('anchors', '不是列表')]:
            with self.subTest(field=field), self.assertRaises(HTTPException):
                validate_extraction({'assets': [{**raw, field: invalid}], 'shots': studio['extractions']['1']['shots']},
                                    studio['documents']['episode-1']['content'], 1, studio['catalog'])

    def test_dns_failure_retry_preserves_completed_setting(self):
        rid = self.create('只写故事设定'); initial = self.step(rid)
        self.message(rid, text='继续写剧本')
        with patch.object(self.host, 'llm_json', side_effect=RuntimeError('无法解析模型接口域名（maas.qianwenaiapi.com）')):
            self.service.work(rid)
        failed = self.service.get(rid)
        self.assertEqual(failed['status'], 'blocked')
        self.assertEqual(failed['studio']['documents'], initial['studio']['documents'])
        self.message(rid, action='retry'); completed = self.step(rid)
        self.assertEqual(completed['studio']['documents']['bible'], initial['studio']['documents']['bible'])
        self.assertEqual(completed['studio']['documents']['episode-2']['version'], 1)

    def test_episode_timeout_retries_only_unfinished_episode_with_updated_model_config(self):
        model = self.host.internal_registry_model('text')
        rid = self.create('写剧本：创作一个 2 集故事', preferences=Preferences(models={'text': model['id']}))
        def timeout_on_episode_two(config, messages):
            if json.loads(messages[-1]['content']).get('task') == 'episode-2':
                raise RuntimeError('模型接口读取超时（300秒），模型可能仍在处理，请稍后重试。')
            return reply(config, messages)
        with patch.object(self.host, 'llm_json', side_effect=timeout_on_episode_two) as requests:
            self.service.work(rid)
            blocked = self.service.get(rid)
            self.assertEqual(blocked['status'], 'blocked')
            self.assertEqual(set(blocked['studio']['documents']), {'bible', 'outline', 'episode-1'})
            self.assertTrue(blocked['retry_warning'])
            completed = deepcopy(blocked['studio']['documents'])
            calls = requests.call_count
            self.service.snapshot(rid, 1)
            self.message(rid, action='resume')
            self.service.work(rid)
            self.assertEqual(requests.call_count, calls)
            self.assertEqual(self.service.get(rid)['status'], 'blocked')
        self.host.update_registry_model('text', model['id'], self.host.RegistryModelUpdate(timeout_seconds=900))
        with patch.object(self.host, 'llm_json', side_effect=reply) as requests:
            self.message(rid, action='retry')
            finished = self.step(rid)
        self.assertEqual(finished['status'], 'completed')
        for key, document in completed.items():
            self.assertEqual(finished['studio']['documents'][key], document)
        self.assertEqual(finished['studio']['documents']['episode-2']['version'], 1)
        requests.assert_called_once()
        self.assertEqual(json.loads(requests.call_args.args[1][-1]['content'])['task'], 'episode-2')
        self.assertEqual(requests.call_args.args[0]['timeout_seconds'], 900)


if __name__ == '__main__': unittest.main()
