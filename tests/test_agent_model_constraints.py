"""Model-aware Agent planning with disposable data and substituted providers."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import agent_runtime
from agent_runtime import Message, Preferences
import test_agent_runtime as runtime_fixture


class AgentModelConstraintTests(unittest.TestCase):
    # Share the isolated provider/database fixture without inheriting its tests.
    setUpClass = classmethod(runtime_fixture.AgentRuntimeTests.setUpClass.__func__)
    tearDownClass = classmethod(runtime_fixture.AgentRuntimeTests.tearDownClass.__func__)
    setUp = runtime_fixture.AgentRuntimeTests.setUp
    model_reply = runtime_fixture.AgentRuntimeTests.model_reply
    add_media = runtime_fixture.AgentRuntimeTests.add_media
    image = runtime_fixture.AgentRuntimeTests.image
    audio = runtime_fixture.AgentRuntimeTests.audio
    video = runtime_fixture.AgentRuntimeTests.video
    create = runtime_fixture.AgentRuntimeTests.create
    step = runtime_fixture.AgentRuntimeTests.step
    confirm = runtime_fixture.AgentRuntimeTests.confirm
    to_storyboard = runtime_fixture.AgentRuntimeTests.to_storyboard

    def configure_video(self, *, provider='ark', durations=None):
        config = {
            'id': 'constraint-video', 'name': '测试视频模型', 'provider_id': provider,
            'provider': 'AI Market' if provider == 'aimarket' else '火山方舟 Ark',
            'model_id': 'minimax-h3' if provider == 'aimarket' else 'test-video',
            'base_url': 'https://provider.invalid', 'api_path': '/tasks',
            'api_key': 'DO-NOT-SEND-THIS-VIDEO-KEY-TO-TEXT-MODEL',
            'capabilities': {'durations': durations or [4, 8], 'resolutions': ['720p'],
                             'aspect_ratios': ['16:9'], 'reference_images': True},
        }
        self.host.save('model_config', config)
        return Preferences(models={'video': config['id']})

    @staticmethod
    def response(value):
        return {'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}]}

    def with_scenes(self, durations):
        def reply(config, messages):
            response = self.model_reply(config, messages)
            if 'scenes:[{id,duration' in messages[0]['content']:
                value = json.loads(response['choices'][0]['message']['content'])
                template = value['scenes'][0]
                value['scenes'] = [dict(deepcopy(template), duration=duration,
                                        description=f'保留情节 {index}',
                                        line_ids=['line_1'] if index == 0 else [])
                                   for index, duration in enumerate(durations)]
                return self.response(value)
            return response
        return reply

    def test_all_creative_steps_receive_selected_model_constraints_without_keys(self):
        preferences = self.configure_video()
        contexts = []

        def reply(config, messages):
            contexts.append(json.loads(messages[-1]['content']))
            return self.model_reply(config, messages)

        with patch.object(self.host, 'llm_json', side_effect=reply):
            rid = self.create(preferences=preferences)
            self.to_storyboard(rid)
        self.assertGreaterEqual(len(contexts), 5)
        for context in contexts:
            constraints = context['model_constraints']['video']
            self.assertEqual(constraints['durations'], [4, 8])
            self.assertEqual(constraints['resolutions'], ['720p'])
            self.assertEqual(constraints['aspect_ratios'], ['16:9'])
            self.assertIn('reference_images', constraints)
            self.assertNotIn('DO-NOT-SEND-THIS-VIDEO-KEY', json.dumps(context))

    def test_unsupported_durations_are_quantized_and_long_scenes_preserve_dialogue(self):
        preferences = self.configure_video()
        with patch.object(self.host, 'llm_json', side_effect=self.with_scenes(['5', 4.2, 19])):
            rid = self.create(preferences=preferences)
            self.to_storyboard(rid)
        run = self.service.get(rid)
        story = run['artifacts']['storyboard']['payload']
        scenes = story['scenes']
        self.assertEqual([scene['duration'] for scene in scenes[:2]], [8, 8])
        self.assertTrue(all(scene['duration'] in [4, 8] for scene in scenes))
        self.assertGreater(len(scenes), 3)
        self.assertGreaterEqual(sum(scene['duration'] for scene in scenes[2:]), 19)
        self.assertEqual([line for scene in scenes for line in scene['line_ids']], ['line_1'])
        for index in range(3):
            self.assertTrue(any(f'保留情节 {index}' in scene['description'] for scene in scenes))
        self.assertEqual(len({scene['id'] for scene in scenes}), len(scenes))
        self.assertEqual(story['total_duration'], sum(scene['duration'] for scene in scenes))
        self.assertNotIn('video', self.provider_calls)

    def test_known_invalid_text_response_repairs_itself_without_user_retry(self):
        rid = self.create()
        analysis_calls = []

        def reply(config, messages):
            if 'estimated_duration:number' in messages[0]['content']:
                analysis_calls.append(json.loads(messages[-1]['content']))
                if len(analysis_calls) == 1:
                    return self.response({'estimated_duration': 0})
            return self.model_reply(config, messages)

        with patch.object(self.host, 'llm_json', side_effect=reply):
            run = self.step(rid)
        self.assertEqual(run['status'], 'waiting_user', run.get('error'))
        self.assertEqual(run['pending_review']['gate'], 'G1')
        self.assertEqual(len(analysis_calls), 2)
        self.assertFalse(self.provider_calls)

    def test_invalid_text_repair_is_bounded(self):
        rid = self.create()
        analysis_calls = []

        def reply(config, messages):
            if 'estimated_duration:number' in messages[0]['content']:
                analysis_calls.append(messages)
                return self.response({'estimated_duration': 0})
            return self.model_reply(config, messages)

        with patch.object(self.host, 'llm_json', side_effect=reply):
            run = self.step(rid)
        self.assertEqual(run['status'], 'blocked')
        self.assertEqual(len(analysis_calls), 3)
        self.assertFalse(self.provider_calls)

    def test_storyboard_repair_keeps_existing_visual_and_voice_assets(self):
        rid = self.create()
        self.step(rid)
        self.confirm(rid)
        self.step(rid)
        before = list(self.provider_calls)
        bindings = deepcopy(self.service.get(rid)['bindings'])
        self.confirm(rid)
        storyboard_calls = []

        def reply(config, messages):
            response = self.model_reply(config, messages)
            if 'scenes:[{id,duration' in messages[0]['content']:
                storyboard_calls.append(messages)
                if len(storyboard_calls) == 1:
                    value = json.loads(response['choices'][0]['message']['content'])
                    value['scenes'][0]['line_ids'] = []
                    return self.response(value)
            return response

        with patch.object(self.host, 'llm_json', side_effect=reply):
            run = self.step(rid)
        self.assertEqual(run['status'], 'waiting_user', run.get('error'))
        self.assertEqual(run['pending_review']['gate'], 'G3')
        self.assertEqual(len(storyboard_calls), 2)
        self.assertEqual(run['bindings'], bindings)
        self.assertEqual(self.provider_calls, before)

    def test_long_formal_speech_extends_plan_and_reuses_audio(self):
        preferences = self.configure_video()
        rid = self.create(preferences=preferences)
        self.to_storyboard(rid)
        self.confirm(rid)
        original_inspect = agent_runtime.inspect_asset

        def inspect(host, asset_id):
            metadata = original_inspect(host, asset_id)
            if '正式台词' in host.require_live_asset(asset_id)['name']:
                metadata = {**metadata, 'duration': 19.0}
            return metadata

        before = list(self.provider_calls)
        with patch.object(agent_runtime, 'inspect_asset', side_effect=inspect):
            self.service.phase_music(rid)
            run = self.service.get(rid)
            saved_speech = deepcopy(run['speech'])
            self.service.phase_music(rid)
        run = self.service.get(rid)
        story = run['artifacts']['storyboard']['payload']
        self.assertEqual(run['phase'], 'video')
        self.assertGreaterEqual(story['total_duration'], 23.25)
        self.assertTrue(all(scene['duration'] in [4, 8] for scene in story['scenes']))
        self.assertEqual([line for scene in story['scenes'] for line in scene['line_ids']], ['line_1'])
        self.assertEqual(run['speech'], saved_speech)
        self.assertEqual(self.provider_calls[len(before):].count('voice'), 1)
        self.assertEqual(self.provider_calls[len(before):].count('music'), 1)
        self.assertEqual(run['planned_inputs'], run['approved_inputs'])
        graph = self.host.creation_context(run['project_id'])
        self.assertEqual(self.service.video_inputs(rid, graph), run['approved_inputs'])

        # Verify the final mix keeps the whole performance across continuation shots.
        clip = self.add_media('video', '模拟视频', run['project_id'])
        self.service.patch(rid, videos={scene['id']: {'asset_id': clip['id']} for scene in story['scenes']})
        with patch.object(agent_runtime, 'render_timeline', return_value=clip):
            self.service.phase_postproduction(rid)
        timeline = self.service.get(rid)['artifacts']['timeline']['payload']
        speech_track = next(item for item in timeline['audio'] if item['asset_id'] == saved_speech['line_1']['asset_id'])
        self.assertEqual(speech_track['end'] - speech_track['start'], 19)
        self.assertGreaterEqual(sum(item['duration'] for item in timeline['clips']), speech_track['end'])

    def test_auto_extension_does_not_overwrite_manual_canvas_change(self):
        canvas = self.host.create_project(self.host.Project(name='手动修改画布', nodes=[]))
        rid = self.create(preferences=self.configure_video(), project_id=canvas['id'])
        self.to_storyboard(rid)
        self.confirm(rid)
        run = self.service.get(rid)
        self.service.materialize_video_node(rid, run['artifacts']['storyboard']['payload']['scenes'][0], 0)
        project = self.host.creation_context(run['project_id'])
        node = next(item for item in project['nodes'] if item['type'] == 'video')
        node['settings']['aspect_ratio'] = '9:16'
        self.host.update_project(project['id'], self.host.Project(**{
            key: project[key] for key in self.host.Project.model_fields if key in project}))
        original_inspect = agent_runtime.inspect_asset

        def inspect(host, asset_id):
            metadata = original_inspect(host, asset_id)
            return {**metadata, 'duration': 19} if '正式台词' in host.require_live_asset(asset_id)['name'] else metadata

        with patch.object(agent_runtime, 'inspect_asset', side_effect=inspect):
            run = self.step(rid)
        self.assertEqual(run['status'], 'blocked')
        self.assertNotIn('video', self.provider_calls)
        updated = self.host.active_project(run['project_id'])
        self.assertEqual(next(item for item in updated['nodes'] if item['id'] == node['id'])['settings']['aspect_ratio'], '9:16')

    def test_h3_generated_local_images_are_video_references(self):
        rid = self.create(preferences=self.configure_video(provider='aimarket'))
        self.to_storyboard(rid)
        run = self.service.get(rid)
        graph = self.host.creation_context(run['project_id'])
        self.assertFalse(any(node['type'] == 'video' for node in graph['nodes']))
        graph = self.service.video_input_project(rid, graph)
        video_nodes = [node for node in graph['nodes'] if node['type'] == 'video']
        self.assertTrue(run['bindings'])
        self.assertTrue(video_nodes)
        for node in video_nodes:
            self.assertEqual(node['settings']['resolution'], '768p')
            self.assertTrue(self.host.video_graph_asset_ids(graph, node))
        constraints = self.service.model_constraints(run)['video']['reference_images']
        self.assertEqual(constraints['transport'], 'data_uri')
        self.assertIsNone(constraints['local_image_fallback'])
        self.assertNotIn('reference_note', run['artifacts']['storyboard']['payload'])

    def test_shot_revision_quantizes_duration_and_preserves_other_shots(self):
        rid = self.create(preferences=self.configure_video())
        self.to_storyboard(rid)
        before = deepcopy(self.service.get(rid)['artifacts']['storyboard']['payload']['scenes'])
        revision = {'kind': 'shots', 'changes': [{'shot_id': 'shot_1', 'duration': '5.2'}],
                    'summary': '延长第一镜。'}
        with patch.object(self.host, 'llm_json', return_value=self.response(revision)):
            self.service.message(rid, Message(request_id='quantize-revision', text='第一镜延长到五秒左右'), 1)
            run = self.step(rid)
        self.assertEqual(run['pending_review']['gate'], 'G3', run.get('error'))
        scenes = run['artifacts']['storyboard']['payload']['scenes']
        self.assertEqual(scenes[0]['duration'], 8)
        self.assertEqual(scenes[0]['line_ids'], before[0]['line_ids'])
        self.assertEqual(scenes[1], before[1])

    def test_rounding_cannot_make_reused_footage_shorter_than_its_plan(self):
        rid = self.create(preferences=self.configure_video())
        self.step(rid)
        self.confirm(rid)
        self.step(rid)
        run = self.service.get(rid)
        clip = self.add_media('video', '用户已有四秒视频', run['project_id'])
        self.service.patch(rid, asset_ids=[clip['id']])
        self.confirm(rid)

        def reply(config, messages):
            response = self.model_reply(config, messages)
            if 'scenes:[{id,duration' in messages[0]['content']:
                value = json.loads(response['choices'][0]['message']['content'])
                value['scenes'][0].update(duration=4.1, video_asset_id=clip['id'])
                return self.response(value)
            return response

        with patch.object(self.host, 'llm_json', side_effect=reply):
            run = self.step(rid)
        self.assertEqual(run['pending_review']['gate'], 'G3', run.get('error'))
        available = agent_runtime.inspect_asset(self.host, clip['id'])['duration']
        for scene in run['artifacts']['storyboard']['payload']['scenes']:
            if scene.get('video_asset_id') == clip['id']:
                self.assertLessEqual(scene['duration'], available + .15)
            else:
                self.assertIn(scene['duration'], [4, 8])

    def test_legacy_duration_failure_resumes_from_cached_plan_without_new_request(self):
        rid = self.create(preferences=self.configure_video())
        self.to_storyboard(rid)
        run = self.service.get(rid)
        op = next(op for op in self.host.listing('agent_operation') if op['run_id'] == rid and op['key'] == 'storyboard:plan')
        response = deepcopy(op['result'])
        response['scenes'][0]['duration'] = 19
        self.host.save('agent_operation', {**op, 'status': 'invalid', 'result': response,
                                         'error': '第 1 镜时长不符合所选视频模型能力'})
        self.service.patch(rid, status='blocked', pending_review=None)
        self.service.message(rid, Message(request_id='resume-old-plan', action='resume'), 1)
        with patch.object(self.host, 'llm_json') as model:
            run = self.step(rid)
            model.assert_not_called()
        self.assertEqual(run['pending_review']['gate'], 'G3', run.get('error'))
        self.assertTrue(all(scene['duration'] in [4, 8] for scene in run['artifacts']['storyboard']['payload']['scenes']))

    def test_negative_creative_review_repairs_the_draft_then_reviews_again(self):
        rid = self.create()
        self.step(rid)
        self.confirm(rid)
        drafts, reviews = [], []

        def reply(config, messages):
            prompt = messages[0]['content']
            if 'visuals:[{id' in prompt:
                drafts.append(json.loads(messages[-1]['content']))
            if '返回 {passed:bool' in prompt:
                reviews.append(messages)
                if len(reviews) == 1:
                    return self.response({'passed': False, 'summary': '需要补充动作衔接', 'issues': ['门口到相拥之间缺少转场']})
            return self.model_reply(config, messages)

        with patch.object(self.host, 'llm_json', side_effect=reply):
            run = self.step(rid)
        self.assertEqual(run['pending_review']['gate'], 'G2', run.get('error'))
        self.assertEqual(len(drafts), 2)
        self.assertEqual(len(reviews), 2)
        self.assertIn('门口到相拥', drafts[1]['repair']['error'])
        self.assertEqual(self.provider_calls.count('image'), 1)


if __name__ == '__main__':
    unittest.main()
