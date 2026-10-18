"""Canvas normalization must not invalidate approved Agent video inputs.

All generation calls use the isolated runtime fixture's local media providers.
The legacy digest helper describes the persisted pre-fix format, so recovery
tests also guard against accidentally approving real edits during migration.
"""
from copy import deepcopy
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException

from agent_runtime import Message, Preferences, fingerprint
import test_agent_runtime as runtime_fixture


class AgentInputGuardTests(unittest.TestCase):
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

    def save_canvas(self, graph):
        return self.host.update_project(graph['id'], self.host.Project(**{
            key: graph[key] for key in self.host.Project.model_fields if key in graph}))

    def normalize_canvas(self, rid, *, editor=True):
        run = self.service.get(rid)
        graph = self.host.active_project(run['project_id'])
        script = next(node for node in graph['nodes'] if node['id'] == run['script_node_id'])
        script['script_data'] = self.host.normalise_script(script['script_data'])
        if editor:
            # Opening the canvas/script editor seeds these fields in app.js.
            for scene in script['script_data']['scenes']:
                if not scene['storyboard_prompt'].strip() and not scene['motion_prompt'].strip():
                    scene['storyboard_prompt'] = self.host.script_scene_prompt(scene)
                scene['final_prompt'] = self.host.script_scene_prompt(scene)
        return self.save_canvas(graph)

    def legacy_inputs(self, rid):
        """Exact old stored digest, intentionally independent of the new guard."""
        run = self.service.get(rid)
        graph = self.service.video_input_project(rid, self.host.active_project(run['project_id']))
        script = next(node for node in graph['nodes'] if node['id'] == run['script_node_id'])
        result = {}
        for index, scene in enumerate(run['artifacts']['storyboard']['payload']['scenes']):
            node_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{rid}:node:{scene["id"]}'))
            node = next(node for node in graph['nodes'] if node['id'] == node_id)
            settings = node.get('settings') or {}
            result[scene['id']] = fingerprint({
                'scene': script['script_data']['scenes'][index],
                'settings': {key: settings.get(key) for key in
                             ('model_config_id', 'resolution', 'aspect_ratio', 'duration')},
                'asset_ids': self.host.video_graph_asset_ids(graph, node),
            })
        return result

    def ready(self, *, materialize_second=False, two_references=False):
        # The default Agent entry now creates a conversation. These assertions
        # exercise actual user canvas edits, so explicitly start in a canvas.
        project = self.host.create_project(self.host.Project(name='输入保护测试画布'))
        rid = self.create(preferences=Preferences(voice=False, music=False), project_id=project['id'])
        self.to_storyboard(rid)
        if materialize_second or two_references:
            run = self.service.get(rid)
            scene = run['artifacts']['storyboard']['payload']['scenes'][1]
            node_id = self.service.materialize_video_node(rid, scene, 1)
            if two_references:
                extra = self.add_media('image', '已确认的第二张参考图', run['project_id'])
                self.service.graph(rid, lambda graph: next(
                    node for node in graph['nodes'] if node['id'] == node_id
                )['settings'].update(asset_ids=[extra['id']]))
                graph = self.host.active_project(run['project_id'])
                self.service.patch(rid, planned_inputs=self.service.video_inputs(rid, graph))
        return rid

    def run_without_render(self, rid):
        project_id = self.service.get(rid)['project_id']
        with patch('agent_runtime.render_timeline', side_effect=lambda *args, **kwargs:
                   self.add_media('video', '模拟完整视频', project_id)):
            return self.step(rid)

    def block_after_first(self, rid, *, legacy=False):
        old_inputs = self.legacy_inputs(rid)
        self.confirm(rid)
        original_event = self.service.event

        def after_result(run_id, kind, text='', **data):
            result = original_event(run_id, kind, text, **data)
            if '第 1/2 段视频已保存' in text:
                self.normalize_canvas(rid)
                if legacy:
                    self.service.patch(rid, planned_inputs=old_inputs, approved_inputs=old_inputs)
                # Reproduce the already-blocked persisted state in the report.
                raise HTTPException(409, '画布中的生成输入已改变，请同步分镜并重新确认。已有成功片段已保留。')
            return result

        with patch.object(self.service, 'event', side_effect=after_result):
            run = self.step(rid)
        self.assertEqual(run['status'], 'blocked', run.get('error'))
        self.assertEqual(set(run['videos']), {'shot_1'})
        return deepcopy(run['videos']['shot_1'])

    def resume(self, rid):
        self.service.message(rid, Message(request_id=str(uuid.uuid4()), action='resume'), 1)
        return self.run_without_render(rid)

    def test_canvas_open_and_save_before_g3_does_not_invalidate_confirmation(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                rid = self.ready()
                run = self.service.get(rid)
                approved_count = len(run['approvals'])
                if legacy:
                    self.service.patch(rid, planned_inputs=self.legacy_inputs(rid))
                self.normalize_canvas(rid, editor=False)
                self.normalize_canvas(rid)
                self.confirm(rid)
                confirmed = self.service.get(rid)
                self.assertEqual(len(confirmed['approvals']), approved_count + 1)
                self.assertIsNone(confirmed.get('pending_review'))
                self.assertEqual(confirmed['phase'], 'music')
                self.assertNotIn('video', self.provider_calls)

    def test_changing_visual_style_invalidates_approved_video_inputs(self):
        rid=self.ready(materialize_second=True)
        self.confirm(rid)
        run=self.service.get(rid)
        graph=self.host.active_project(run['project_id'])
        before=self.service.video_inputs(rid,graph)
        node=next(n for n in graph['nodes'] if n.get('type')=='video')
        node['settings']['visual_style']={'id':'wax-crayon-storybook'}
        self.assertNotEqual(self.service.video_inputs(rid,graph),before)

    def test_canvas_normalization_while_first_video_finishes_keeps_second_running(self):
        rid = self.ready()
        self.confirm(rid)
        original_event = self.service.event
        first_assets = []

        def normalize_after_result(run_id, kind, text='', **data):
            result = original_event(run_id, kind, text, **data)
            if '第 1/2 段视频已保存' in text:
                first_assets.append(self.service.get(rid)['videos']['shot_1']['asset_id'])
                self.normalize_canvas(rid)
            return result

        with patch.object(self.service, 'event', side_effect=normalize_after_result):
            run = self.run_without_render(rid)
        self.assertEqual(run['status'], 'waiting_user', run.get('error'))
        self.assertEqual(run['pending_review']['gate'], 'G4')
        self.assertEqual(self.provider_calls.count('video'), 2)
        self.assertEqual(run['videos']['shot_1']['asset_id'], first_assets[0])
        graph = self.host.active_project(run['project_id'])
        self.assertEqual(len([node for node in graph['nodes'] if node['type'] == 'video']), 2)

    def test_resume_legacy_normalization_block_preserves_first_video_and_approval(self):
        rid = self.ready()
        first = self.block_after_first(rid, legacy=True)
        approvals = deepcopy(self.service.get(rid)['approvals'])
        run = self.resume(rid)
        self.assertEqual(run['status'], 'waiting_user', run.get('error'))
        self.assertEqual(run['pending_review']['gate'], 'G4')
        self.assertEqual(self.provider_calls.count('video'), 2)
        self.assertEqual(run['videos']['shot_1'], first)
        self.assertEqual(run['approvals'], approvals)

    def edit_second_input(self, rid, change):
        run = self.service.get(rid)
        graph = self.host.active_project(run['project_id'])
        script = next(node for node in graph['nodes'] if node['id'] == run['script_node_id'])
        scene = script['script_data']['scenes'][1]
        second_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{rid}:node:shot_2'))
        second = next(node for node in graph['nodes'] if node['id'] == second_id)
        if change == 'prompt':
            scene['storyboard_prompt'] = '用户把第二镜改为门口的俯拍全景'
            scene['final_prompt'] = self.host.script_scene_prompt(scene)
        elif change == 'scene_duration':
            scene['duration'] = '8s'
        elif change in ('duration', 'aspect_ratio', 'resolution', 'model_config_id'):
            second['settings'][change] = {
                'duration': 8, 'aspect_ratio': '9:16', 'resolution': '1080p',
                'model_config_id': 'user-changed-model',
            }[change]
        elif change == 'reference_image':
            replacement = self.add_media('image', '用户新参考图', run['project_id'])
            second['settings']['asset_ids'] = [replacement['id']]
        elif change == 'reference_order':
            refs = self.host.video_graph_asset_ids(graph, second)
            self.assertGreaterEqual(len(refs), 2)
            second['settings']['reference_order'] = list(reversed(refs))
        elif change == 'delete_video':
            graph['nodes'] = [node for node in graph['nodes'] if node['id'] != second_id]
            graph['edges'] = [edge for edge in graph['edges']
                              if second_id not in (edge['from'], edge['to'])]
        elif change == 'delete_script':
            graph['nodes'] = [node for node in graph['nodes'] if node['id'] != script['id']]
            graph['edges'] = [edge for edge in graph['edges']
                              if script['id'] not in (edge['from'], edge['to'])]
        else:
            raise AssertionError(change)
        self.save_canvas(graph)

    def test_real_changes_after_normalization_still_block_new_and_legacy_runs(self):
        for legacy in (False, True):
            for change in ('prompt', 'scene_duration', 'duration', 'aspect_ratio',
                           'resolution', 'model_config_id', 'reference_image',
                           'reference_order', 'delete_video', 'delete_script'):
                with self.subTest(legacy=legacy, change=change):
                    rid = self.ready(materialize_second=True, two_references=True)
                    first = self.block_after_first(rid, legacy=legacy)
                    calls_before = list(self.provider_calls)
                    approved_before = deepcopy(self.service.get(rid)['approved_inputs'])
                    self.edit_second_input(rid, change)
                    run = self.resume(rid)
                    self.assertEqual(run['status'], 'blocked', change)
                    self.assertEqual(run['videos'], {'shot_1': first})
                    self.assertEqual(self.provider_calls, calls_before)
                    self.assertEqual(run['approved_inputs'], approved_before)
                    self.assertTrue('改变' in run['error'] or '删除' in run['error'], run['error'])


if __name__ == '__main__':
    unittest.main()
