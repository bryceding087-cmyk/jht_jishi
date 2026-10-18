"""Image dimensions, exact batch count, partial failures and retry identity; no paid calls."""
import io
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi import HTTPException
from pydantic import ValidationError
from test_canvas_groups import CanvasGroupsTests as Storage
from image_service import image_capabilities, image_output_size, IMAGE_RATIOS


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
