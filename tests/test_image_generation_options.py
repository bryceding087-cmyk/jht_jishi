"""Image dimensions, exact batch count, partial failures and retry identity; no paid calls."""
import io
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi import HTTPException
from pydantic import ValidationError
from test_canvas_groups import CanvasGroupsTests as Storage
from image_service import image_capabilities, image_output_size, generated_image_name, IMAGE_RATIOS


class GeneratedImageNameTests(unittest.TestCase):
    def test_mentions_become_short_reference_labels(self):
        names = ['生成一个大熊猫.jpg', '微信图片_2026-10-18_114611_958.png', '生成一个女人.jpg']
        prompt = f'将@{names[0]}的背景换成 @{names[1]}，并将熊猫本体换成 @{names[2]} 中的女人'
        self.assertEqual(generated_image_name(prompt, names), '图1的背景换成图2')
        self.assertEqual(generated_image_name('把@背景(1).png改成冬日街景', ['背景(1).png']), '图1改成冬日街景')
        self.assertEqual(generated_image_name('将@参考图高清调整为暖色', ['参考图', '参考图高清']), '图2调整为暖色')

    def test_titles_are_short_safe_and_have_fallbacks(self):
        self.assertEqual(generated_image_name('请帮我生成一张清晨的城市天际线，柔和光线'), '清晨的城市天际线')
        self.assertEqual(generated_image_name('@参考.png', ['参考.png']), '参考图创作')
        self.assertEqual(generated_image_name('＠已改名的图片.png'), '参考图创作')
        self.assertEqual(generated_image_name('...'), 'AI 生成图片')
        title = generated_image_name('城市夜景与霓虹灯下的街道' * 8)
        self.assertLessEqual(len(title), 24)
        self.assertTrue(title.startswith('城市夜景'))
        self.assertEqual(generated_image_name('a/b\\c:d*e?更多内容'), 'a b c d e')


class ImageGenerationOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Storage.setUpClass();cls.host=Storage.host

    @classmethod
    def tearDownClass(cls):Storage.tearDownClass()

    def setUp(self):
        with self.host.connect() as db:db.execute('DELETE FROM records')
        self.config={'id':'test-image','model':'doubao-seedream-5-0-pro-260628','api_key':'fixture-key','base_url':'https://fixture.invalid','api_path':'/images/generations'}
        output=io.BytesIO();Image.new('RGB',(64,64),'blue').save(output,'PNG');self.image=output.getvalue()

    def enqueue(self,**changes):
        request=self.host.ImageGenerationRequest(prompt='城市光影',name='城市光影',request_id='batch-request',**changes)
        with patch.object(self.host,'internal_image_config',return_value=self.config),patch.object(self.host.threading,'Thread'):
            return self.host.create_image_generation(request)

    def test_resolution_profiles_and_explicit_ratio_dimensions(self):
        self.assertEqual(image_capabilities(self.config)['resolutions'],['1K','1.5K','2K'])
        self.assertEqual(image_capabilities({'model':'doubao-seedream-5-0-lite'})['resolutions'],['2K','3K','4K'])
        for size in ['1K','1.5K','2K','3K','4K']:
            self.assertEqual(image_output_size(size,'adaptive'),size)
            for ratio in IMAGE_RATIOS[1:]:
                width,height=map(int,image_output_size(size,ratio).split('x'));a,b=map(int,ratio.split(':'))
                self.assertLess(abs(width/height-a/b),.025)
                self.assertEqual(width%16,0);self.assertEqual(height%16,0)

    def test_exact_count_provider_payload_storage_and_idempotency(self):
        task=self.enqueue(size='1.5K',aspect_ratio='3:4',count=3)
        with patch.object(self.host,'provider_json',return_value={'data':[{'url':'https://fixture.invalid/image.png'}]}) as upstream,patch.object(self.host,'download_generated_image',return_value=(self.image,'.png')):
            self.host.run_image_generation(task['id'],self.config)
        result=self.host.image_generation_status(task['id'])
        self.assertEqual(result['status'],'succeeded');self.assertEqual(len(result['assets']),3)
        self.assertEqual(len({a['id'] for a in result['assets']}),3)
        self.assertEqual(upstream.call_count,3)
        self.assertEqual(upstream.call_args.args[3]['size'],image_output_size('1.5K','3:4'))
        self.assertIs(task['watermark'],False)
        self.assertIs(upstream.call_args.args[3]['watermark'],False)
        self.assertNotIn('count',upstream.call_args.args[3])
        self.assertEqual(self.enqueue(size='1.5K',aspect_ratio='3:4',count=3)['id'],task['id'])
        with self.assertRaises(HTTPException) as changed:self.enqueue(size='1.5K',aspect_ratio='3:4',count=4)
        self.assertEqual(changed.exception.status_code,409)

    def test_automatic_names_preserve_full_prompt_references_and_retry_identity(self):
        names = ['生成一个大熊猫.jpg', '微信图片_2026-10-18_114611_958.png', '生成一个女人.jpg']
        references = [self.host.store_uploaded_asset(self.image, name) for name in names]
        prompt = f'将@{names[0]}的背景换成@{names[1]}，并将熊猫本体换成@{names[2]}中的女人'
        request = self.host.ImageGenerationRequest(prompt=prompt, count=2, request_id='short-names',
            reference_asset_ids=[asset['id'] for asset in references])
        with patch.object(self.host, 'internal_image_config', return_value=self.config), patch.object(self.host.threading, 'Thread'):
            task = self.host.create_image_generation(request)
        with patch.object(self.host, 'provider_json', return_value={'data':[{'url':'https://fixture.invalid/image.png'}]}) as upstream, patch.object(self.host, 'download_generated_image', return_value=(self.image,'.png')):
            self.host.run_image_generation(task['id'], self.config)
        result = self.host.image_generation_status(task['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([asset['name'] for asset in result['assets']], ['图1的背景换成图2-1.png', '图1的背景换成图2-2.png'])
        self.assertEqual(result['prompt'], prompt)
        self.assertEqual(upstream.call_count, 2)
        for call in upstream.call_args_list:
            self.assertEqual(call.args[3]['prompt'], prompt)
            self.assertEqual(len(call.args[3]['image']), 3)
        details = self.host.asset_details(result['asset']['id'])
        self.assertEqual(details['generation']['prompt'], prompt)
        self.assertEqual(len(details['generation']['references']), 3)
        # Reference labels may change, but retrying a request must reuse its original result.
        self.host.save('asset', {**self.host.get('asset', references[0]['id']), 'name':'已重命名.png'})
        with patch.object(self.host.threading, 'Thread') as worker:
            retried = self.host.create_image_generation(request)
        self.assertEqual(retried['id'], task['id'])
        self.assertEqual(retried['asset']['name'], result['asset']['name'])
        worker.assert_not_called()

    def test_explicit_image_name_is_preserved(self):
        name = '冬日城市的人物海报_手动指定的完整名称_最终版本.png'
        request = self.host.ImageGenerationRequest(prompt='重新绘制人物和背景', name=name, request_id='explicit-name')
        with patch.object(self.host, 'internal_image_config', return_value=self.config), patch.object(self.host.threading, 'Thread'):
            task = self.host.create_image_generation(request)
        with patch.object(self.host, 'provider_json', return_value={'data':[{'url':'https://fixture.invalid/image.png'}]}), patch.object(self.host, 'download_generated_image', return_value=(self.image,'.png')):
            self.host.run_image_generation(task['id'], self.config)
        self.assertEqual(self.host.image_generation_status(task['id'])['asset']['name'], name)

    def test_cached_client_and_legacy_task_cannot_enable_watermark(self):
        task=self.enqueue(watermark=True)
        self.assertIs(task['watermark'],False)
        self.host.save('image_task',{**self.host.get('image_task',task['id']),'watermark':True})
        with patch.object(self.host,'provider_json',return_value={'data':[{'url':'https://fixture.invalid/image.png'}]}) as upstream,patch.object(self.host,'download_generated_image',return_value=(self.image,'.png')):
            self.host.run_image_generation(task['id'],self.config)
        self.assertIs(upstream.call_args.args[3]['watermark'],False)

    def test_partial_result_survives_later_provider_failure(self):
        task=self.enqueue(count=4)
        with patch.object(self.host,'provider_json',side_effect=[{'data':[{'url':'https://fixture.invalid/1.png'}]},RuntimeError('fixture failure')]) as upstream,patch.object(self.host,'download_generated_image',return_value=(self.image,'.png')):
            self.host.run_image_generation(task['id'],self.config)
        result=self.host.image_generation_status(task['id'])
        self.assertEqual(result['status'],'failed');self.assertEqual(len(result['assets']),1)
        self.assertEqual(result['asset'],result['assets'][0]);self.assertEqual(upstream.call_count,2)

    def test_selected_style_reaches_actual_image_provider_request(self):
        task=self.enqueue(visual_style={'id':'feature-animation'})
        with patch.object(self.host,'provider_json',return_value={'data':[{'url':'https://fixture.invalid/image.png'}]}) as upstream,patch.object(self.host,'download_generated_image',return_value=(self.image,'.png')):
            self.host.run_image_generation(task['id'],self.config)
        wire=upstream.call_args.args[3]
        self.assertEqual(wire['prompt'],task['effective_prompt'])
        self.assertIn('圆润动画电影3D',wire['prompt'])
        self.assertEqual(task['prompt'],'城市光影')
        self.assertNotIn('image',wire)
        self.assertNotIn('visual_style',wire)
        with self.assertRaises(HTTPException) as changed:self.enqueue(visual_style=None)
        self.assertEqual(changed.exception.status_code,409)

    def test_invalid_count_ratio_and_unsupported_model_size_rejected(self):
        for changes in [{'count':0},{'count':5},{'aspect_ratio':'7:11'},{'size':'8K'}]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):self.host.ImageGenerationRequest(prompt='test',**changes)
        with self.assertRaises(HTTPException) as unsupported:self.enqueue(size='4K')
        self.assertEqual(unsupported.exception.status_code,422)

    def test_registry_exposes_real_video_and_image_options(self):
        self.assertIs(self.host.ImageGenerationRequest(prompt='test').watermark,False)
        self.assertIs(self.host.DEFAULT_IMAGE_MODEL['watermark'],False)
        image=self.host.registry_public('image',{**self.config,'is_default':True})
        self.assertEqual(image['capabilities']['counts'],[1,2,3,4])
        video=self.host.registry_public('video',self.host.internal_default_model())
        self.assertEqual(video['capabilities']['durations'],list(range(4,16)))
        self.assertIn('16:9',video['capabilities']['aspect_ratios'])
        self.assertEqual(video['capabilities']['resolutions'],['768p','2k'])


if __name__=='__main__':unittest.main()
