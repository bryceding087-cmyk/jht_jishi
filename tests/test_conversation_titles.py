"""Initial naming without extra provider calls or changes to manually named chats."""
import unittest
import test_sidebar_metadata as fixture
from conversation_titles import short_title
from conversation_service import refine_initial_title


class ShortTitleTests(unittest.TestCase):
    def test_subjects_and_explicit_work_titles(self):
        for prompt, expected in [
            ('帮我生成武松打虎@', '武松打虎'),
            ('创建一个小女孩在打篮球的视频', '小女孩打篮球'),
            ('请帮我生成一张窗边的橘猫图片，暖色调，16:9', '窗边的橘猫'),
            ('帮我改编《施工员小夫》为十集短剧', '施工员小夫'),
            ('生成图片：雨后的小镇', '雨后的小镇'),
            ('武松打虎 @reference.png', '武松打虎'),
        ]:
            with self.subTest(prompt=prompt):
                self.assertEqual(short_title(prompt), expected)

    def test_length_empty_and_english(self):
        self.assertLessEqual(len(short_title('小夫初入工地经历一系列挑战最终成为优秀的施工负责人')), 16)
        self.assertEqual(short_title('请帮我生成', 'image'), '图片创作')
        self.assertEqual(short_title('Please create a picture of sunset beach'), 'sunset beach')
        self.assertEqual(short_title('画布节点说明', summarize=False), '画布节点说明')
        self.assertEqual(short_title('写作技巧'), '写作技巧')


class ConversationTitleTests(fixture.SidebarMetadataTests):
    def test_auto_title_and_retry_preserve_manual_name(self):
        for kind in ('image', 'video', 'audio'):
            body = {'kind': kind, 'prompt': '创建一个小女孩在打篮球的视频', 'request_id': 'title-' + kind}
            status, _, item = self.request('POST', '/api/conversations', body)
            self.assertEqual(status, 201)
            self.assertEqual(item['title'], '小女孩打篮球')
            path = '/api/conversations/' + item['id']
            self.request('PATCH', path + '/metadata', {'title': '我取的名字'})
            self.assertIsNone(refine_initial_title(self.host, item['id'], '少女篮球梦'))
            self.assertEqual(self.request('POST', '/api/conversations', body)[2]['title'], '我取的名字')
            self.assertEqual(self.request('GET', path)[2]['title'], '我取的名字')

    def test_refinement_once_preserves_other_fields_and_deleted_chats(self):
        body = {'kind': 'image', 'prompt': '新入施工员小夫刚到工地开始了工作', 'request_id': 'initial'}
        item = self.request('POST', '/api/conversations', body)[2]
        path = '/api/conversations/' + item['id']
        self.request('PATCH', path + '/metadata', {'pinned': True})
        self.assertEqual(refine_initial_title(self.host, item['id'], '小夫初入工地'), '小夫初入工地')
        self.assertIsNone(refine_initial_title(self.host, item['id'], '第二轮消息'))
        saved = self.request('GET', path)[2]
        self.assertEqual(saved['name'], saved['title'])
        self.assertEqual(saved['title'], '小夫初入工地')
        self.assertTrue(saved['pinned'])
        self.assertEqual(saved['prompt'], body['prompt'])
        body['request_id'] = 'deleted'
        deleted = self.request('POST', '/api/conversations', body)[2]
        self.request('DELETE', '/api/conversations/' + deleted['id'])
        self.assertIsNone(refine_initial_title(self.host, deleted['id'], '不能修改'))

    def test_explicit_and_legacy_names_are_not_refined(self):
        item = self.request('POST', '/api/conversations', {
            'title': '明确指定名称', 'kind': 'image', 'prompt': '猫', 'request_id': 'explicit',
        })[2]
        self.assertIsNone(refine_initial_title(self.host, item['id'], '橘猫'))
        value = self.host.get('conversation', item['id'])
        value.pop('title_source')
        self.host.save('conversation', value)
        self.assertIsNone(refine_initial_title(self.host, item['id'], '旧对话勿改'))
