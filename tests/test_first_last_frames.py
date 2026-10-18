"""Frame ratios, positional roles and turn-specific input ordering."""
import io
from unittest.mock import patch
from PIL import Image
import test_video_audio_references


class FirstLastFrameTests(test_video_audio_references.VideoAudioReferenceTests):
    def test_canvas_frames_use_selected_order_and_ignore_other_graph_references(self):
        first, last = self.frame(160, 90), self.frame(100, 100)
        graph = {'id': 'canvas-frames', 'nodes': [
            {'id': 'video', 'type': 'video', 'settings': {'asset_ids': [self.image['id'], first['id'], last['id']]}}
        ], 'edges': []}
        params = self.host.Task(project_id=graph['id'], node_id='video', prompt='frames', mode='first-last', asset_ids=[last['id'], first['id']])
        with patch.object(self.host, 'resolve_model_config', return_value=self.config()):
            task = self.host._submit_video_task(params, graph)
        self.assertEqual(task['params']['asset_ids'], [last['id'], first['id']])
        self.assertEqual(task['params']['mode'], 'first-last')

    def test_canvas_frames_reject_unconnected_asset(self):
        from fastapi import HTTPException
        other = self.frame(100, 100)
        graph = {'id': 'canvas-frames', 'nodes': [
            {'id': 'video', 'type': 'video', 'settings': {'asset_ids': [self.image['id']]}}
        ], 'edges': []}
        params = self.host.Task(project_id=graph['id'], node_id='video', prompt='frames', mode='first-last', asset_ids=[self.image['id'], other['id']])
        with self.assertRaises(HTTPException) as caught:
            self.host._submit_video_task(params, graph)
        self.assertEqual(caught.exception.status_code, 422)

    def frame(self, width, height):
        buffer = io.BytesIO()
        Image.new('RGB', (width, height), 'blue').save(buffer, format='PNG')
        return self.host.store_uploaded_asset(buffer.getvalue(), 'frame.png', '')

    def submit_frames(self, frames, ratio="16:9", resolution="720p"):
        graph = {'id': 'frame-conversation', 'creation_type': 'conversation', 'nodes': [
            {'id': 'video', 'type': 'video', 'settings': {'asset_ids': [self.image['id']]}}
        ], 'edges': []}
        params = self.host.Task(project_id=graph['id'], node_id='video', prompt='首尾帧测试', mode='first-last', asset_ids=frames, aspect_ratio=ratio, resolution=resolution)
        with patch.object(self.host, 'resolve_model_config', return_value=self.config()):
            return self.host._submit_video_task(params, graph)

    def test_equal_ratio_different_sizes_keep_first_last_roles_and_order(self):
        first, last = self.frame(160, 90), self.frame(320, 180)
        task = self.submit_frames([last['id'], first['id']])
        self.assertEqual(task['params']['mode'], 'first-last')
        self.assertEqual(task['params']['asset_ids'], [last['id'], first['id']])
        content = self.host.provider_payload(task['params'], self.config())['content']
        self.assertEqual([item['role'] for item in content[1:]], ['first_frame', 'last_frame'])
        self.assertTrue(content[1]['image_url']['url'].startswith('data:image/'))

    def test_different_frame_ratios_preserve_user_output_ratio_and_resolution(self):
        first, last = self.frame(160, 90), self.frame(100, 100)
        task = self.submit_frames([first['id'], last['id']], ratio='9:16', resolution='1080p')
        self.assertEqual(task['params']['aspect_ratio'], '9:16')
        self.assertEqual(task['params']['resolution'], '1080p')
        self.assertEqual(task['params']['mode'], 'first-last')
        payload = self.host.provider_payload(task['params'], self.config())
        self.assertEqual(payload['ratio'], '9:16')
        self.assertEqual(payload['resolution'], '1080p')
        self.assertEqual([item['role'] for item in payload['content'][1:]], ['first_frame', 'last_frame'])

    def test_same_asset_can_fill_both_positional_slots(self):
        frame = self.frame(160, 90)
        task = self.submit_frames([frame['id'], frame['id']])
        self.assertEqual(task['params']['asset_ids'], [frame['id'], frame['id']])
        content = self.host.provider_payload(task['params'], self.config())['content']
        self.assertEqual([item['role'] for item in content[1:]], ['first_frame', 'last_frame'])
