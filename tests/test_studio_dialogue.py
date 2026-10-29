"""Production notes are not dialogue; real spoken text remains protected."""
import unittest
from fastapi import HTTPException
from studio_graph import scenes_for, scene_dialogue, validate_extraction
from test_studio_extraction_references import SCRIPT, catalog, shots


class StudioDialogueTests(unittest.TestCase):
    def test_appendices_end_last_scene_preserving_global_lines(self):
        text = SCRIPT + '\n## 三、资产登记与提示词\n统一美术：低饱和写实纪实摄影，9:16竖构图。\n- facts：24岁施工员。\n## 四、制作画布\n合计：120秒。'
        scenes = scenes_for(text, 5)
        self.assertEqual([s['line_start'] for s in scenes], [2, 7])
        self.assertNotIn('统一美术', scenes[-1]['content'])
        self.assertIn('欢迎回家', scenes[-1]['content'])
        self.assertIn('统一美术', scenes_for(text, 5, include_appendices=True)[-1]['content'])
        result = validate_extraction({'assets': [], 'shots': shots()}, text, 5, catalog(), frozen_catalog=True)
        self.assertEqual(len(result['shots']), 2)
        missing = shots(); missing[-1]['dialogue'] = ''
        with self.assertRaises(HTTPException) as error:
            validate_extraction({'assets': [], 'shots': missing}, text, 5, catalog(), frozen_catalog=True)
        self.assertIn('欢迎回家', error.exception.detail)

    def test_peer_sections_end_scenes_but_shot_subheadings_do_not(self):
        text = '## 场1-1\n### 镜头1\n小夫：走吧。\n## 镜头2\n老周：等等。\n## 制作说明\n风格：写实。'
        scene = scenes_for(text, 1)[0]
        self.assertIn('老周：等等。', scene['content'])
        self.assertNotIn('制作说明', scene['content'])

    def test_metadata_and_markdown_speakers(self):
        text = '时间：午后\n统一美术：冷灰调。\n- facts：工地施工员。\n音效：风声。\n字幕：住宅项目。\n**小夫（画外）：**结论：今晚返工。\n- 老周：我补。\n旁白：两人回到窗边。\n光影：这是我的名字。'
        self.assertEqual(scene_dialogue(text, ['小夫', '老周', '光影']), [
            ('小夫（画外）', '结论：今晚返工。'), ('老周', '我补。'), ('旁白', '两人回到窗边。'), ('光影', '这是我的名字。')])

    def test_colon_inside_real_dialogue_is_not_discarded(self):
        text = SCRIPT.replace('欢迎回家。', '结论：今晚返工。')
        value = shots(); value[-1]['dialogue'] = '小夫：今晚返工。'
        with self.assertRaises(HTTPException):
            validate_extraction({'assets': [], 'shots': value}, text, 5, catalog())
        value[-1]['dialogue'] = '小夫：结论：今晚返工。'
        validate_extraction({'assets': [], 'shots': value}, text, 5, catalog())

    def test_split_utterance_and_unregistered_speaker_remain_checked(self):
        text = SCRIPT.replace('小夫：欢迎回家。', '工友（画外）：欢迎回家。')
        value = shots(); value[-1]['dialogue'] = '工友：欢迎\n工友：回家。'
        validate_extraction({'assets': [], 'shots': value}, text, 5, catalog())
        value[-1]['dialogue'] = '工友：欢迎。'
        with self.assertRaises(HTTPException):
            validate_extraction({'assets': [], 'shots': value}, text, 5, catalog())
