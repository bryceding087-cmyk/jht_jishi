import unittest
from conversation_service import conversation_covers


class ConversationCoverTests(unittest.TestCase):
    def covers(self, assets=(), tasks=()):
        class Store:
            def listing(self, kind):
                return assets if kind == 'asset' else tasks
        return conversation_covers(Store(), {'chat'})

    def test_latest_generated_visual_wins_and_inputs_audio_other_chats_are_ignored(self):
        assets = [
            {'project_id': 'chat', 'kind': 'image', 'url': '/old.png', 'source': 'generated', 'created_at': 1},
            {'project_id': 'chat', 'kind': 'video', 'url': '/latest.mp4', 'source': 'generated', 'created_at': 2},
            {'project_id': 'chat', 'kind': 'image', 'url': '/reference.png', 'source': 'uploaded', 'created_at': 9},
            {'project_id': 'chat', 'kind': 'audio', 'url': '/voice.wav', 'source': 'generated', 'created_at': 10},
            {'project_id': 'other', 'kind': 'image', 'url': '/other.png', 'source': 'generated', 'created_at': 11},
        ]
        self.assertEqual(self.covers(assets)['chat']['url'], '/latest.mp4')

    def test_video_task_before_saving_to_asset_and_newer_image(self):
        task = {'project_id': 'chat', 'status': 'done', 'video_url': '/video.mp4', 'updated_at': 2}
        self.assertEqual(self.covers(tasks=[task])['chat']['kind'], 'video')
        image = {'project_id': 'chat', 'kind': 'image', 'url': '/new.png', 'source': 'generated', 'created_at': 3}
        self.assertEqual(self.covers([image], [task])['chat']['url'], '/new.png')

    def test_deleted_output_and_deleted_or_incomplete_turns_are_excluded(self):
        assets = [{'project_id': 'chat', 'kind': 'video', 'url': '/deleted.mp4', 'source': 'generated', 'created_at': 4, 'deleted_at': 5}]
        tasks = [{'project_id': 'chat', 'status': 'done', 'video_url': '/deleted.mp4', 'updated_at': 4},
                 {'project_id': 'chat', 'status': 'done', 'video_url': '/turn.mp4', 'deleted_at': 6},
                 {'project_id': 'chat', 'status': 'running', 'video_url': '/unfinished.mp4'}]
        self.assertEqual(self.covers(assets, tasks), {})


if __name__ == '__main__':
    unittest.main()
