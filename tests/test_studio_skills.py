"""Offline skill revisions and the production node optimization lifecycle."""
from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from agent_runtime import AgentService, Preferences
from studio_graph import build_graph
from studio_prompt_optimizer import (DIRECTION_PREFIX, MAX_PROMPT_CHARS,
    optimization_context, production_context, prompt_targets)
from studio_skills import SKILL_ROOT, load_skill_bundle
import test_agent_studio as studio_fixture
from studio_fixtures import reply


def settings(node):
    return node['settings'] if node['type'] == 'video' else node['image_settings']


def optimization_batches(mock):
    return [json.loads(call.args[1][-1]['content']) for call in mock.call_args_list
            if json.loads(call.args[1][-1]['content']).get('task') == 'optimize_node_prompts']


def optimization_calls(mock):
    result = []
    for batch in optimization_batches(mock):
        shared = batch['shared']
        for raw in batch['nodes']:
            node = {**deepcopy(shared), **deepcopy(raw)}
            node['production'] = {**shared.get('production', {}), **raw.get('production', {})}
            node['production']['assets'] = [shared['assets'][key] for key in node['production'].pop('asset_keys', [])]
            result.append(node)
    return result


class StudioSkillBundleTests(unittest.TestCase):
    def test_reviewed_sources_and_stage_selection(self):
        image = load_skill_bundle('image')
        video = load_skill_bundle('video')
        writing = load_skill_bundle('writing')
        self.assertIn('@user_b26ab084/seedance-prompt-expert', image['metadata']['skills'])
        self.assertIn('@davtime/manjuduanjushengcheng', video['metadata']['skills'])
        self.assertIn('@org-bzwfrdvh/aipdd-movie-tools', writing['metadata']['skills'])
        self.assertNotIn('@user_b26ab084/seedance-prompt-expert', writing['metadata']['skills'])
        self.assertNotEqual(image['metadata']['version'], video['metadata']['version'])
        manifest = json.loads((SKILL_ROOT / 'manifest.json').read_text(encoding='utf-8'))
        for entry in manifest['skills']:
            self.assertRegex(entry['archive_sha256'], r'^[a-f0-9]{64}$')
            self.assertTrue(entry['source_files'])

    def test_rule_edit_invalidates_revision_and_missing_rules_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'skills'
            shutil.copytree(SKILL_ROOT, root)
            with patch('studio_skills.SKILL_ROOT', root):
                before = load_skill_bundle('image')['metadata']['version']
                path = root / 'seedance-prompt-expert.md'
                path.write_text(path.read_text(encoding='utf-8') + '\n新增空间连续性要求。', encoding='utf-8')
                self.assertNotEqual(before, load_skill_bundle('image')['metadata']['version'])
                path.unlink()
                with self.assertRaises(HTTPException):
                    load_skill_bundle('image')

    def test_context_carries_related_identities_without_secrets_or_other_assets(self):
        node = {'id': 'clip', 'type': 'video', 'settings': {'prompt': '沿用首帧', 'duration': 5},
                'mujian': {'inputs': [{'nodeId': 'frame'}]}}
        graph = {'nodes': [node, {'id': 'frame', 'mujian': {'inputs': [{'nodeId': 'actor'}]}},
                           {'id': 'actor', 'mujian': {'definitionId': 'c1'}}]}
        studio = {'style_bible': '明亮卡通3D', 'brief': {'constraints': '保留对白'}, 'catalog': [
            {'key': 'c1', 'type': 'character', 'facts': '小孩', 'design': '黄色外衣', 'anchors': ['卷发'],
             'assetUrl': 'https://private.invalid', 'api_key': 'secret'},
            {'key': 'other', 'design': '其他作品的角色'}]}
        production = production_context(node, graph, studio)
        context = optimization_context(node, {'id': 'video', 'api_key': 'secret'}, 'v1',
            production=production, text_model={'id': 'text', 'api_key': 'secret'})
        self.assertEqual(context['production']['assets'][0]['design'], '黄色外衣')
        self.assertEqual(context['production']['style_bible'], '明亮卡通3D')
        self.assertNotIn('secret', json.dumps(context))
        self.assertNotIn('private.invalid', json.dumps(context))
        self.assertEqual(len(context['production']['assets']), 1)
        context['production']['assets'][0]['anchors'].clear()
        self.assertEqual(studio['catalog'][0]['anchors'], ['卷发'])


class StudioSkillIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        studio_fixture.AgentStudioTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    setUp = studio_fixture.AgentStudioTests.setUp
    create = studio_fixture.AgentStudioTests.create
    step = studio_fixture.AgentStudioTests.step
    message = studio_fixture.AgentStudioTests.message
    model_reply = staticmethod(reply)
    add_media = studio_fixture.AgentStudioTests.add_media
    image = studio_fixture.AgentStudioTests.image
    audio = studio_fixture.AgentStudioTests.audio
    video = studio_fixture.AgentStudioTests.video

    def test_every_generation_node_uses_skills_and_keeps_native_contract(self):
        rid = self.create(preferences=Preferences(studio_video_mode='reference', style='明亮动物卡通3D',
            visual_style={'id': 'custom', 'custom_prompt': '明亮动物卡通3D，圆润轮廓与柔和材质。'}))
        run = self.step(rid)
        graph = self.host.active_project(run['studio']['canvas_project_id'])
        base = build_graph(run)
        originals = {node['id']: node for node in base['nodes']}
        targets = prompt_targets(graph)
        calls = optimization_calls(self.host.llm_json)
        self.assertEqual({c['node_id'] for c in calls}, {n['id'] for n in targets})
        self.assertEqual(len(calls), len(targets))
        self.assertTrue(all(c['visual_style']['prompt'] == '明亮动物卡通3D，圆润轮廓与柔和材质。' for c in calls))
        self.assertEqual({n['mujian']['role'] for n in targets}, {'reference', 'shot'})
        self.assertLess(len(optimization_batches(self.host.llm_json)), len(targets))
        self.assertTrue(all(len(batch['nodes']) <= 6 for batch in optimization_batches(self.host.llm_json)))
        self.assertEqual({n['mujian']['assetType'] for n in targets if n['mujian']['role'] == 'reference'}, {'character', 'scene', 'prop'})
        for node in targets:
            original = originals[node['id']]
            self.assertTrue(settings(node)['prompt'].startswith(settings(original)['prompt'] + DIRECTION_PREFIX))
            self.assertEqual({k: v for k, v in settings(node).items() if k != 'prompt'},
                             {k: v for k, v in settings(original).items() if k != 'prompt'})
            self.assertEqual(node['mujian']['inputs'], original['mujian']['inputs'])
            meta = node['mujian']['promptOptimization']
            self.assertEqual(meta['status'], 'applied')
            self.assertIn('@user_b26ab084/seedance-prompt-expert', meta['skills'])
            self.assertLessEqual(len(settings(node)['prompt']), MAX_PROMPT_CHARS)
        self.assertEqual(graph['edges'], base['edges'])
        self.assertEqual(run['studio']['prompt_optimization_progress']['current'], len(targets))
        self.assertEqual(run['studio']['prompt_optimization_progress']['status'], 'completed')
        self.assertFalse(self.provider_calls)

    def test_legacy_modes_use_direct_references_in_agent_production(self):
        for mode in ('text', 'first', 'reference'):
            with self.subTest(mode=mode):
                rid = self.create(preferences=Preferences(studio_video_mode=mode))
                self.host.llm_json.reset_mock()
                run = self.step(rid)
                videos = [c for c in optimization_calls(self.host.llm_json) if c['media_type'] == 'video']
                self.assertTrue(videos)
                self.assertTrue(all(c['mode'] == 'reference' for c in videos))
                self.assertTrue(all(c['references'] for c in videos))
                self.assertTrue(all(c['production']['assets'] for c in videos))
                self.assertEqual(run['status'], 'completed')

    def test_sync_reuses_checkpoints_and_changed_style_invalidates_them(self):
        rid = self.create(); initial = self.step(rid)
        pid = initial['studio']['canvas_project_id']
        graph = self.host.active_project(pid)
        node = prompt_targets(graph)[0]
        node['x'] = 923
        settings(node)['prompt'] = '用户手动确定的角色外观'
        self.host.update_project(pid, self.host.Project(**graph))
        self.host.llm_json.reset_mock()
        self.message(rid, action='studio_canvas'); self.step(rid)
        self.assertEqual(optimization_calls(self.host.llm_json), [])
        saved = next(n for n in self.host.active_project(pid)['nodes'] if n['id'] == node['id'])
        self.assertEqual(settings(saved)['prompt'], '用户手动确定的角色外观')
        self.assertEqual(saved['x'], 923)
        self.assertEqual(saved['mujian']['promptOptimization']['status'], 'proposed')
        self.assertIn(DIRECTION_PREFIX, saved['mujian']['proposed']['image_settings']['prompt'])
        self.service.studio_patch(rid, style_bible='保持全剧3D卡通材质，以既定暖色主光表现轮廓。')
        self.host.llm_json.reset_mock()
        self.message(rid, action='studio_canvas'); self.step(rid)
        self.assertEqual(len(optimization_calls(self.host.llm_json)), len(prompt_targets(graph)))
        saved = next(n for n in self.host.active_project(pid)['nodes'] if n['id'] == node['id'])
        self.assertEqual(settings(saved)['prompt'], '用户手动确定的角色外观')

    def test_pause_after_received_answer_resumes_without_duplicate_request(self):
        rid = self.create(); node_ids, received_batches = [], []
        def pause(config, messages):
            context = json.loads(messages[-1]['content'])
            answer = reply(config, messages)
            if context.get('task') == 'optimize_node_prompts':
                node_ids.extend(node['node_id'] for node in context['nodes'])
                received_batches.append(context)
                if len(received_batches) == 2:
                    self.message(rid, action='pause')
            return answer
        with patch.object(self.host, 'llm_json', side_effect=pause):
            self.service.work(rid)
        paused = self.service.get(rid)
        self.assertEqual(paused['status'], 'paused')
        self.assertFalse(paused['studio']['canvas_project_id'])
        self.assertEqual(len(node_ids), len(set(node_ids)))
        self.assertGreaterEqual(len(received_batches), 2)
        self.assertLessEqual(len(received_batches), 3)
        self.assertTrue(set(paused['studio'].get('prompt_optimizations', {})).issubset(node_ids))
        receipts = [op for op in self.host.listing('agent_operation')
                    if op['run_id'] == rid and op['key'].startswith('studio-prompt-batch:')]
        self.assertEqual(len([op for op in receipts if op['status'] == 'succeeded']), len(received_batches))
        restored = AgentService(self.host)
        with patch.object(restored, 'kick'), patch.object(self.host, 'llm_json', side_effect=reply) as requests:
            from agent_runtime import Message
            restored.message(rid, Message(request_id='resume-skills', action='resume'), 1)
            restored.work(rid)
        finished = restored.get(rid)
        self.assertEqual(finished['status'], 'completed', finished.get('error'))
        self.assertFalse({c['node_id'] for c in optimization_calls(requests)} & set(node_ids))
        self.assertEqual(len(finished['studio']['prompt_optimizations']), len(prompt_targets(build_graph(finished))))

    def test_invalid_direction_repairs_once_and_retry_reuses_completed_nodes(self):
        rid = self.create(); seen = []
        def invalid(config, messages):
            context = json.loads(messages[-1]['content'])
            if context.get('task') == 'optimize_node_prompts':
                seen.extend(node['node_id'] for node in context['nodes'])
                return {'choices': [{'message': {'content': json.dumps({'items': [
                    {'node_id': node['node_id'], 'direction': '保持既定人物身份、造型与空间关系。' if node['node_id'] == seen[0] else '重写参考@其他角色'}
                    for node in context['nodes']]})}}]}
            return reply(config, messages)
        with patch.object(self.host, 'llm_json', side_effect=invalid):
            self.service.work(rid)
        blocked = self.service.get(rid)
        self.assertEqual(blocked['status'], 'blocked')
        counts = Counter(seen)
        self.assertGreaterEqual(len(counts), 2)
        self.assertEqual(counts[seen[0]], 1)
        self.assertTrue(all(count in (1, 2) for node_id, count in counts.items() if node_id != seen[0]))
        self.assertTrue(any(count == 2 for count in counts.values()))
        self.assertFalse(blocked['studio']['canvas_project_id'])
        self.assertEqual(set(blocked['studio']['prompt_optimizations']), {seen[0]})
        with patch.object(self.host, 'llm_json', side_effect=reply) as requests:
            self.message(rid, action='retry'); finished = self.step(rid)
        self.assertNotIn(seen[0], {c['node_id'] for c in optimization_calls(requests)})
        self.assertEqual(blocked['studio']['documents'], finished['studio']['documents'])
        self.assertFalse(self.provider_calls)

    def test_unknown_result_never_resubmits_on_resume(self):
        rid = self.create()
        def timeout(config, messages):
            if json.loads(messages[-1]['content']).get('task') == 'optimize_node_prompts':
                raise RuntimeError('模型接口读取超时')
            return reply(config, messages)
        with patch.object(self.host, 'llm_json', side_effect=timeout) as requests:
            self.service.work(rid)
            initial = optimization_calls(requests)
            self.assertGreaterEqual(len(initial), 1)
            self.assertLessEqual(len(optimization_batches(requests)), 3)
            self.assertEqual(len({c['node_id'] for c in initial}), len(initial))
            self.assertTrue(self.service.get(rid)['retry_warning'])
            self.message(rid, action='resume'); self.service.work(rid)
            self.assertEqual(optimization_calls(requests), initial)
        self.assertFalse(self.host.listing('project'))
        self.assertFalse(self.provider_calls)

    def test_save_conflict_only_remerges_and_keeps_new_manual_edits(self):
        rid = self.create(); run = self.step(rid)
        pid = run['studio']['canvas_project_id']; update = self.host.update_project
        attempts = []
        def conflict(project_id, body):
            attempts.append(project_id)
            if len(attempts) == 1:
                latest = self.host.active_project(pid)
                prompt_targets(latest)[0]['x'] = 1234
                update(pid, self.host.Project(**latest))
                raise HTTPException(409, '画布版本变化')
            return update(project_id, body)
        self.host.llm_json.reset_mock()
        with patch.object(self.host, 'update_project', side_effect=conflict):
            self.message(rid, action='studio_canvas'); self.step(rid)
        self.assertEqual(len(attempts), 2)
        self.assertFalse(optimization_calls(self.host.llm_json))
        self.assertEqual(prompt_targets(self.host.active_project(pid))[0]['x'], 1234)

    def test_prompt_capacity_preflight_prevents_partial_calls_or_truncation(self):
        rid = self.create(); run = self.step(rid)
        graph = build_graph(run)
        targets = prompt_targets(graph)
        settings(targets[-1])['prompt'] = '原' * MAX_PROMPT_CHARS
        before = deepcopy(graph)
        self.host.llm_json.reset_mock()
        with self.assertRaises(HTTPException) as error:
            self.service.studio_optimize_prompts(rid, graph)
        self.assertIn('容量不足', str(error.exception.detail))
        self.assertFalse(self.host.llm_json.called)
        self.assertEqual(graph, before)


if __name__ == '__main__':
    unittest.main()
