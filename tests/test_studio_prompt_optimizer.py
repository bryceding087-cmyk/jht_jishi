"""Prompt optimization is append-only and cannot change canvas bindings."""
from copy import deepcopy
import unittest

from fastapi import HTTPException

from studio_graph import merge_graph
from visual_styles import compose_visual_prompt
from studio_prompt_optimizer import (DIRECTION_PREFIX, MAX_PROMPT_CHARS,
    MIN_DIRECTION_CHARS, apply_direction, mark_merged_optimizations,
    optimization_context, optimization_digest, prompt_targets, validate_direction)


def image_node(role='reference'):
    return {'id': 'image-1', 'type': 'asset', 'title': '小林 · 首帧', 'x': 20, 'y': 30,
        'image_settings': {'prompt': '@小林 保持灰色外套，右手握着旧信。', 'aspect_ratio': '3:4',
            'model_config_id': 'image-model', 'count': 1,
            'mentions': [{'token': '@小林', 'node_id': 'hero', 'name': '小林', 'kind': 'image', 'asset_id': 'real-asset'}]},
        'mujian': {'runId': 'run-1', 'role': role, 'assetType': 'character',
            'inputs': [{'nodeId': 'hero', 'kind': 'image', 'role': '人物身份', 'slot': ''}],
            'sources': [{'episode': 1, 'scene': '1-1', 'line': 5, 'quote': '△小林握着旧信。'}]}}


def video_node():
    return {'id': 'video-1', 'type': 'video', 'title': '第一段', 'x': 100, 'y': 30,
        'settings': {'prompt': '@首帧\n分镜 1（0–6 秒）：固定机位。\n原文对白：小林：我回来了。',
            'negative_prompt': '', 'aspect_ratio': '16:9', 'duration': 6, 'model_config_id': 'video-model',
            'asset_ids': ['reference'], 'reference_order': ['reference']},
        'mujian': {'runId': 'run-1', 'role': 'shot', 'mode': 'first', 'inputs': [],
            'shots': [{'key': 's1', 'seconds': 6, 'dialogue': '小林：我回来了。', 'camera': '固定机位'}]}}


class StudioPromptOptimizerTests(unittest.TestCase):
    def test_targets_exclude_documents_audio_and_timelines(self):
        images = [image_node(role) for role in ('reference', 'firstFrame', 'lastFrame')]
        prop = image_node(); prop.update(id='prop', type='prop')
        video = video_node()
        graph = {'nodes': [*images, prop, video,
            {'id': 'doc', 'type': 'prompt', 'settings': {'prompt': '剧本正文'}},
            {'id': 'edit', 'type': 'timeline', 'settings': {'prompt': '不应优化'}},
            {'id': 'audio', 'type': 'audio', 'audio_settings': {'prompt': '配音'}},
            {'id': 'empty', 'type': 'asset', 'image_settings': {'prompt': '  '}}]}
        targets = prompt_targets(graph)
        self.assertEqual(targets, [*images, prop, video])
        self.assertIs(targets[0], images[0])

    def test_context_contains_only_public_model_and_local_read_only_facts(self):
        node = image_node()
        node['assetUrl'] = 'https://signed.invalid/private'
        model = {'id': 'image-model', 'name': '图片模型', 'model_id': 'public-model',
            'capabilities': {'max_images': 9}, 'api_key': 'secret', 'base_url': 'https://private.invalid'}
        context = optimization_context(node, model, 'skills-v1')
        self.assertEqual(set(context['model']), {'id', 'name', 'model_id', 'capabilities'})
        self.assertEqual(context['base_prompt'], node['image_settings']['prompt'])
        self.assertEqual(context['references'], node['mujian']['inputs'])
        self.assertNotIn('asset_id', context['mentions'][0])
        self.assertNotIn('assetUrl', context)
        context['references'][0]['nodeId'] = 'malicious'
        context['model']['capabilities']['max_images'] = 100
        self.assertEqual(node['mujian']['inputs'][0]['nodeId'], 'hero')
        self.assertEqual(model['capabilities']['max_images'], 9)
        video = optimization_context(video_node(), {}, 'skills-v1')
        self.assertEqual((video['media_type'], video['mode'], video['duration']), ('video', 'first', 6))
        self.assertEqual(video['shots'][0]['dialogue'], '小林：我回来了。')

    def test_digest_changes_for_prompt_skill_model_and_storyboard(self):
        context = optimization_context(video_node(), {'id': 'v1'}, 'skills-v1')
        original = optimization_digest(context)
        self.assertEqual(original, optimization_digest(dict(reversed(list(context.items())))))
        for key, value in [('base_prompt', '改变'), ('skill_revision', 'skills-v2'), ('model', {'id': 'v2'}), ('shots', [])]:
            altered = deepcopy(context); altered[key] = value
            self.assertNotEqual(original, optimization_digest(altered))

    def test_output_shape_cannot_mutate_graph_or_provider_settings(self):
        context = optimization_context(image_node(), {}, 'v1')
        for result in (None, [], '文字', {}, {'direction': None}, {'direction': 3}, {'direction': '   '},
                       {'direction': '清晰呈现衣料纹理。', 'settings': {'model_config_id': 'other'}},
                       {'direction': '清晰呈现衣料纹理。', 'nodes': []}):
            with self.subTest(result=result), self.assertRaises(HTTPException):
                validate_direction(result, context)

    def test_new_reference_notation_is_rejected(self):
        context = optimization_context(image_node(), {}, 'v1')
        for text in ('参考@新角色', '参考＠新角色', '图片1中的人物', '图片 １２', '图片一中的人物',
                     '视频二作为结尾', '音频3作为对白', '音频 三', 'Image 1 is the actor', 'figure2', 'AUDIO1'):
            with self.subTest(text=text), self.assertRaises(HTTPException):
                validate_direction({'direction': text}, context)
        self.assertEqual(validate_direction({'direction': '保持现有参考身份，衣料纹理清晰。'}, context), '保持现有参考身份，衣料纹理清晰。')

    def test_budget_accounts_for_separator_without_truncating_original(self):
        node = image_node()
        node['image_settings']['prompt'] = '原' * (MAX_PROMPT_CHARS - len(DIRECTION_PREFIX) - MIN_DIRECTION_CHARS)
        context = optimization_context(node, {}, 'v1')
        self.assertEqual(context['remaining_chars'], MIN_DIRECTION_CHARS)
        base = node['image_settings']['prompt']
        apply_direction(node, '细' * MIN_DIRECTION_CHARS, {'skills': ['expert'], 'version': 'v1'}, 'input')
        self.assertEqual(len(node['image_settings']['prompt']), MAX_PROMPT_CHARS)
        self.assertTrue(node['image_settings']['prompt'].startswith(base + DIRECTION_PREFIX))
        with self.assertRaises(HTTPException):
            validate_direction({'direction': '细' * (MIN_DIRECTION_CHARS + 1)}, context)
        full = image_node(); full['image_settings']['prompt'] = '原' * MAX_PROMPT_CHARS
        self.assertEqual(optimization_context(full, {}, 'v1')['remaining_chars'], 0)
        with self.assertRaises(HTTPException): apply_direction(full, '指导', {}, 'input')
        self.assertEqual(full['image_settings']['prompt'], '原' * MAX_PROMPT_CHARS)

    def test_selected_visual_style_is_in_context_and_uses_provider_prompt_budget(self):
        node = image_node()
        node['image_settings'].update(prompt='原' * 3400,
            visual_style={'id': 'custom', 'custom_prompt': '明亮动物卡通3D，圆润轮廓、柔和材质。' * 12})
        context = optimization_context(node, {}, 'v1')
        self.assertEqual(context['visual_style']['id'], 'custom')
        effective = compose_visual_prompt(context['base_prompt'], context['visual_style'])
        self.assertEqual(context['remaining_chars'], MAX_PROMPT_CHARS - len(effective) - len(DIRECTION_PREFIX))
        with self.assertRaises(HTTPException):
            validate_direction({'direction': '细' * (context['remaining_chars'] + 1)}, context)
        original_digest = optimization_digest(context)
        apply_direction(node, '细' * context['remaining_chars'], {'skills': ['expert']}, original_digest)
        self.assertEqual(len(compose_visual_prompt(node['image_settings']['prompt'], context['visual_style'])), MAX_PROMPT_CHARS)
        node['image_settings']['prompt'] = context['base_prompt']
        node['image_settings']['visual_style']['custom_prompt'] = '手绘水彩'
        self.assertNotEqual(original_digest, optimization_digest(optimization_context(node, {}, 'v1')))

    def test_apply_preserves_every_native_parameter_and_is_idempotent(self):
        for node in (image_node(), video_node()):
            before = deepcopy(node)
            settings_key = 'settings' if node['type'] == 'video' else 'image_settings'
            context = optimization_context(node, {'id': 'model'}, 'v1')
            key = optimization_digest(context)
            direction = '保持原有动作和机位，明确手与旧信的接触关系。'
            apply_direction(node, direction, {'skills': ['expert'], 'version': 'v1', 'arbitrary': 'ignored'}, key)
            self.assertEqual(node[settings_key]['prompt'], before[settings_key]['prompt'] + DIRECTION_PREFIX + direction)
            optimized = deepcopy(node)
            apply_direction(node, direction, {'skills': ['expert'], 'version': 'v1'}, key)
            self.assertEqual(node, optimized)
            del node['mujian']['promptOptimization']
            node[settings_key]['prompt'] = before[settings_key]['prompt']
            self.assertEqual(node, before)

    def test_merge_preserves_manual_prompt_and_marks_new_guidance_as_proposed(self):
        base = {'name': '画布', 'nodes': [image_node(), video_node()], 'edges': [], 'groups': [], 'viewport': {}}
        old = merge_graph(deepcopy(base), None, 'run-1')
        old['nodes'][0]['image_settings']['prompt'] = '用户自定构图'
        old['nodes'][0]['image_settings']['model_config_id'] = 'user-model'
        old['nodes'][0]['x'] = 777
        fresh = deepcopy(base)
        for node in fresh['nodes']:
            context = optimization_context(node, {}, 'v1')
            apply_direction(node, '主体轮廓清晰，保持原有机位和叙事。', {'skills': ['expert'], 'version': 'v1'}, optimization_digest(context))
        proposal = deepcopy(fresh)
        merged = merge_graph(fresh, old, 'run-1')
        mark_merged_optimizations(merged, proposal)
        saved = merged['nodes'][0]
        self.assertEqual(saved['image_settings']['prompt'], '用户自定构图')
        self.assertEqual(saved['image_settings']['model_config_id'], 'user-model')
        self.assertEqual(saved['x'], 777)
        self.assertEqual(saved['mujian']['promptOptimization']['status'], 'proposed')
        self.assertEqual(saved['mujian']['proposed']['image_settings']['prompt'], proposal['nodes'][0]['image_settings']['prompt'])
        self.assertEqual(saved['mujian']['definitionHash'], merge_graph(deepcopy(proposal), None, 'run-1')['nodes'][0]['mujian']['definitionHash'])
        clean = merge_graph(deepcopy(proposal), None, 'run-1')
        mark_merged_optimizations(clean, proposal)
        self.assertTrue(all(n['mujian']['promptOptimization']['status'] == 'applied' for n in clean['nodes']))

    def test_prior_applied_provenance_is_not_attributed_to_new_skill(self):
        old_node = image_node()
        apply_direction(old_node, '旧版指导', {'skills': ['old-expert'], 'version': 'old'}, 'old-input')
        new_node = image_node()
        apply_direction(new_node, '新版指导', {'skills': ['new-expert'], 'version': 'new'}, 'new-input')
        graph = {'nodes': [old_node]}
        mark_merged_optimizations(graph, {'nodes': [new_node]})
        metadata = old_node['mujian']['promptOptimization']
        self.assertEqual(metadata['status'], 'proposed')
        self.assertEqual(metadata['skills'], ['new-expert'])
        self.assertEqual(metadata['previousApplied']['skills'], ['old-expert'])
        self.assertTrue(old_node['image_settings']['prompt'].endswith('旧版指导'))


if __name__ == '__main__':
    unittest.main()
