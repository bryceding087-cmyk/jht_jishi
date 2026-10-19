"""Style catalogue, API persistence and provider contracts; no paid calls."""
import hashlib
import json
from collections import Counter
from itertools import product
from pathlib import Path
from urllib.parse import urlsplit
import unittest
from pydantic import ValidationError

from visual_styles import (VisualStyleSelection, style_catalog, public_styles,
                           resolve_style, compose_visual_prompt, style_task_fields)
from provider_adapters import video_payload
import test_conversation_generation as generation_fixture
from test_auth import Browser


class VisualStyleTests(unittest.TestCase):
    def test_catalogue_and_all_previews_are_complete(self):
        styles = style_catalog()['styles']
        self.assertEqual(style_catalog()['version'], '2.3.0')
        self.assertEqual(len(styles), 152)
        self.assertEqual(len({s['id'] for s in styles}), 152)
        self.assertEqual(Counter(s['category'] for s in styles), {'真人': 53, '2D': 44, '3D': 35, '建筑': 20})
        for style in styles:
            with self.subTest(style=style['id']):
                self.assertNotIn('preview_subject', style)
                self.assertNotIn('preview_prompt', style)
                url = urlsplit(style['preview_url'])
                preview = Path(__file__).resolve().parents[1] / 'public' / url.path.lstrip('/')
                self.assertTrue(preview.is_file())
                self.assertEqual(hashlib.sha256(preview.read_bytes()).hexdigest(), style['preview_sha256'])
                self.assertEqual(url.query, 'v=' + style['preview_sha256'][:12])
                snapshot = resolve_style({'id': style['id']})
                self.assertEqual(snapshot['prompt'], style['image_style_prompt_zh'])
                for kind in ('image', 'video'):
                    text = compose_visual_prompt('一只猫在街道行走', snapshot, kind)
                    self.assertTrue(text.startswith('一只猫在街道行走'))
                    self.assertIn(style['display_name'], text)
                    self.assertLessEqual(len(text), 4000)
        self.assertNotIn('image_style_prompt_zh', public_styles()['styles'][0])

    def test_architecture_preserves_existing_building_and_user_directions(self):
        for style in style_catalog()['styles']:
            if style['category'] != '建筑':
                continue
            with self.subTest(style=style['id']):
                fields = style_task_fields({'id': style['id']}, '原有教学楼，平视构图，固定机位', 'video')
                self.assertIn('保留体量、层数、开口、结构和相机视角', fields['effective_prompt'])
                self.assertIn('除非用户明确要求改造', fields['effective_prompt'])
                self.assertNotIn('构图建议', fields['effective_prompt'])
                self.assertNotIn('运动建议', fields['effective_prompt'])
                self.assertEqual(fields['style_snapshot']['version'], '2.3.0')

    def test_invalid_or_forged_selection_rejected(self):
        for value in [{'id': 'missing'}, {'id': 'custom'}, {'id': 'custom', 'custom_prompt': '  '},
                      {'id': 'custom', 'custom_prompt': '长' * 1501},
                      {'id': 'feature-animation', 'custom_prompt': '替换正式配方'},
                      {'id': 'feature-animation', 'prompt': '伪造配方'}]:
            with self.subTest(value=str(value)[:70]), self.assertRaises(ValidationError):
                VisualStyleSelection.model_validate(value)

    def test_no_style_and_custom_prompt(self):
        self.assertIsNone(resolve_style(None))
        self.assertEqual(compose_visual_prompt('原文\n保留', None), '原文\n保留')
        self.assertEqual(style_task_fields(None, '原文', 'image'), {})
        snapshot = resolve_style({'id': 'custom', 'custom_prompt': '  纸雕材质，柔和侧光  '})
        self.assertEqual(snapshot['prompt'], '纸雕材质，柔和侧光')
        self.assertIn('纸雕材质，柔和侧光', compose_visual_prompt('纯白背景，俯拍', snapshot))

    def test_explicit_art_directions_override_preset_defaults(self):
        snapshot = {'id': 'fixture', 'name': '测试', 'prompt': '纸雕材质，米黄纸张背景，红色配色，强烈逆光，居中特写',
                    'avoid': ['彩色画面'], 'video_motion': '快速推进', 'video_consistency': ['保持材质一致']}
        prompt = '纯白背景，蓝色配色，柔光，俯拍构图，固定机位'
        text = compose_visual_prompt(prompt, snapshot, 'video')
        self.assertIn('纸雕材质', text)
        for conflict in ('米黄纸张', '红色配色', '强烈逆光', '居中特写', '彩色画面', '快速推进'):
            self.assertNotIn(conflict, text)
        self.assertIn('保持材质一致', text)

    def test_combined_length_is_validated_without_truncation(self):
        with self.assertRaisesRegex(ValueError, '4000'):
            style_task_fields({'id': 'feature-animation'}, '长' * 3999, 'image')

    def test_video_providers_receive_effective_text_without_extra_reference_images(self):
        for style_id, (provider, model, resolution) in product(
                ('wax-crayon-storybook', 'architecture-parametric-fluid'),
                [('ark', 'seedance-test', '720p'), ('aimarket', 'minimax-h3', '768p')]):
            fields = style_task_fields({'id': style_id}, '一辆蓝色卡车，固定镜头', 'video')
            with self.subTest(style=style_id, provider=provider):
                wire = video_payload({'prompt': '一辆蓝色卡车，固定镜头', **fields, 'asset_ids': [],
                    'aspect_ratio': '16:9', 'resolution': resolution, 'duration': 4, 'mode': 'text-to-video'},
                    {'provider_id': provider, 'model_id': model}, lambda _: self.fail('Style preview must not be a reference'))
                self.assertEqual(wire['content'], [{'type': 'text', 'text': fields['effective_prompt']}])
                self.assertNotIn('visual_style', wire)
                self.assertNotIn('style_snapshot', wire)


class StyleWorkflowTests(unittest.TestCase):
    """Reuse an isolated ASGI fixture, without inheriting its existing tests."""
    fixture = generation_fixture.ConversationGenerationTests
    setUpClass = classmethod(fixture.setUpClass.__func__)
    tearDownClass = classmethod(fixture.tearDownClass.__func__)
    setUp = fixture.setUp
    tearDown = fixture.tearDown
    fixture_setup = fixture.fixture_setup
    login = fixture.login
    request = fixture.request
    upload = fixture.upload
    owned_save = fixture.owned_save
    payload = fixture.payload
    route = fixture.route
    finish = fixture.finish

    def test_catalogue_requires_login_and_members_can_select(self):
        self.assertEqual(Browser(self.h.app).request('GET', '/api/visual-styles')[0], 401)
        self.assertEqual(len(self.request(self.a, 'GET', '/api/visual-styles')['styles']), 152)

    def test_task_snapshot_history_clear_and_idempotency(self):
        for kind, style_id in product(('image', 'video'), ('feature-animation', 'architecture-parametric-fluid')):
            style = {'id': style_id}
            name = resolve_style(style)['name']
            request_id = kind + '-' + style_id
            with self.subTest(kind=kind, style=style_id):
                context = self.request(self.a, 'POST', '/api/conversations', {'title': '风格测试', 'kind': kind,
                    'prompt': '原始描述', 'settings': {'visual_style': style}, 'request_id': request_id}, 201)
                body = {**self.payload(kind, context, request_id + '-styled'), 'visual_style': style}
                task = self.request(self.a, 'POST', self.route(kind), body, 202)
                params = task if kind == 'image' else task['params']
                self.assertEqual(params['style_snapshot']['name'], name)
                self.assertEqual(params['style_snapshot']['version'], '2.3.0')
                self.assertIn(name, params['effective_prompt'])
                self.assertEqual(params['prompt'], '测试')
                again = self.request(self.a, 'POST', self.route(kind), body, 202)
                self.assertEqual(again['id'], task['id'])
                self.finish(kind, task)
                self.request(self.a, 'POST', self.route(kind), {**body, 'visual_style': None}, 409)
                cleared = self.request(self.a, 'POST', self.route(kind), {**body, 'request_id': request_id + '-clear', 'visual_style': None}, 202)
                self.finish(kind, cleared)
                detail = self.request(self.a, 'GET', '/api/conversations/' + context['id'])
                turns = {t['id']: t for t in detail['tasks']}
                self.assertEqual(turns[task['id']]['generation_settings']['visual_style']['id'], style['id'])
                for key in ('visual_style', 'style_snapshot', 'effective_prompt'):
                    self.assertIsNone(turns[cleared['id']]['generation_settings'][key])
                self.request(self.b, 'GET', '/api/conversations/' + context['id'], expected=404)


if __name__ == '__main__':
    unittest.main()
