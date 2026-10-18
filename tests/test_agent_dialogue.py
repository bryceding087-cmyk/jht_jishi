"""Public dialogue must reflect actual creative scope and saved media."""
from copy import deepcopy
import unittest

from agent_dialogue import DIALOGUE_PROMPT, dialogue


class AgentDialogueTests(unittest.TestCase):
    def setUp(self):
        self.run = {
            'source': '小猫咪咪学钓鱼，经历分心后学会耐心。',
            'working_story': '小猫咪咪学钓鱼，经历分心后学会耐心。',
            'phase': 'storyboard', 'status': 'running',
            'preferences': {'scope': 'full_video', 'voice': True, 'music': True, 'subtitles': True},
            'artifacts': {
                'director': {'payload': {'title': '咪咪学钓鱼', 'locked_lines': [{'id': 'line_1', 'text': '我学会啦！'}]}},
                'creative': {'payload': {'title': '咪咪学钓鱼', 'visuals': [{'name': '湖边的咪咪'}], 'speakers': [{'name': '咪咪'}]}},
                'storyboard': {'payload': {'scenes': [
                    {'id': 'shot_1', 'duration': 4, 'description': '咪咪在湖边等待'},
                    {'id': 'shot_2', 'duration': 8, 'description': '咪咪收获第一条鱼'}]}},
            },
            'bindings': {}, 'videos': {}, 'speech': {}, 'bgm': None,
        }

    def test_prompt_requests_public_choices_not_hidden_reasoning(self):
        self.assertIn('user_message', DIALOGUE_PROMPT)
        self.assertIn('不输出私密思维链', DIALOGUE_PROMPT)
        self.assertIn('不得声称搜索过', DIALOGUE_PROMPT)
        self.assertIn('不要在 user_message 中报告', DIALOGUE_PROMPT)

    def test_story_specific_optional_copy_is_used_with_real_next_step(self):
        result = {'user_message': '我会把咪咪从分心到耐心的变化放在动作里，让钓到鱼的结尾更有回报感。'}
        message = dialogue('creative', self.run, result)
        self.assertIn(result['user_message'], message)
        self.assertIn('准备方案中的参考素材', message)
        self.assertNotIn('已生成', message)

    def test_missing_optional_copy_still_mentions_actual_story(self):
        self.assertIn('咪咪学钓鱼', dialogue('received', self.run))
        self.assertIn('湖边的咪咪', dialogue('creative', self.run))

    def test_model_tool_or_hidden_reasoning_claims_are_not_relayed(self):
        for value in ('我已搜索热点并看过所有视频。', '我已经生成了参考图片。', '图片已经准备好了。', '视频已经合成。', '逐步推理：先假设用户喜欢悬疑。',
                      '<script>alert(1)</script>', {'text': '不是字符串'}, 'x' * 221):
            with self.subTest(value=value):
                message = dialogue('creative', self.run, {'user_message': value})
                self.assertIn('湖边的咪咪', message)
                self.assertNotIn('热点', message)
                self.assertNotIn('逐步推理', message)

    def test_storyboard_only_has_no_media_promise(self):
        self.run['preferences']['scope'] = 'storyboard'
        for stage in ('received', 'creative', 'storyboard', 'delivery'):
            with self.subTest(stage=stage):
                message = dialogue(stage, self.run)
                self.assertNotIn('制作配音', message)
                self.assertNotIn('准备方案中的参考素材', message)
                self.assertNotIn('生成视频', message)
        message = dialogue('storyboard', self.run)
        self.assertIn('2 段', message)
        self.assertIn('12 秒', message)
        self.assertIn('交付分镜', message)

    def test_no_voice_music_avoids_imaginary_production(self):
        self.run['preferences'].update(voice=False, music=False)
        message = dialogue('storyboard', self.run, {'user_message': '我会用温柔旁白和音乐突出耐心。'})
        self.assertNotIn('旁白', message)
        self.assertNotIn('配音', message)
        self.assertNotIn('BGM', message)
        self.assertEqual(dialogue('music_ready', self.run), '')
        message = dialogue('review', self.run, duration=12)
        self.assertNotIn('台词听感', message)
        self.assertNotIn('配乐', message)

    def test_no_lines_does_not_promise_formal_voice(self):
        self.run['artifacts']['director']['payload']['locked_lines'] = []
        message = dialogue('storyboard', self.run)
        self.assertNotIn('正式配音', message)
        self.assertIn('整片 BGM', message)

    def test_assets_names_only_materialized_kinds(self):
        self.run['bindings'] = {'v1': {'kind': 'image', 'asset_id': 'image-1'},
                                'v2': {'kind': 'image'}, 'a1': {'kind': 'audio', 'asset_id': 'audio-1'}}
        self.run['preferences']['voice'] = False
        message = dialogue('assets', self.run)
        self.assertIn('1 张参考图片', message)
        self.assertNotIn('音色', message)
        self.run['preferences']['voice'] = True
        message = dialogue('assets', self.run)
        self.assertIn('1 段音色试听', message)
        self.assertNotIn('正式配音', message)

    def test_music_ready_requires_real_asset_handle(self):
        self.run['bgm'] = {'duration': 12}
        self.run['speech'] = {'line_1': {'duration': 1}}
        self.assertEqual(dialogue('music_ready', self.run), '')
        self.run['bgm']['asset_id'] = 'music-1'
        self.assertIn('整片 BGM已准备好', dialogue('music_ready', self.run))
        self.assertNotIn('正式配音', dialogue('music_ready', self.run))

    def test_normalized_storyboard_counts_override_authored_numbers(self):
        result = {'user_message': '我把故事安排为3段，预计30秒。',
                  'scenes': self.run['artifacts']['storyboard']['payload']['scenes'], 'total_duration': 999}
        message = dialogue('storyboard', self.run, result)
        self.assertIn('2 段，计划 12 秒', message)
        self.assertNotIn('30秒', message)
        self.assertNotIn('999', message)

    def test_video_progress_tells_story_beat_without_claiming_visual_review(self):
        message = dialogue('video_saved', self.run, index=1)
        self.assertIn('第 1/2 段', message)
        self.assertIn('咪咪在湖边等待', message)
        self.assertNotIn('画面检查通过', message)
        self.assertIn('接下来制作下一段', message)
        self.assertEqual(dialogue('video_saved', self.run, index=3), '')

    def test_postproduction_only_reports_present_audio_and_all_clips(self):
        self.run['videos'] = {'shot_1': {'asset_id': 'video-1'}}
        self.assertEqual(dialogue('postproduction', self.run), '')
        self.run['videos']['shot_2'] = {'asset_id': 'video-2'}
        message = dialogue('postproduction', self.run)
        self.assertNotIn('BGM', message)
        self.assertNotIn('配音', message)
        self.run['bgm'] = {'asset_id': 'music-1'}
        self.assertIn('前面准备的整片 BGM', dialogue('postproduction', self.run))

    def test_resume_counts_only_current_storyboard_assets(self):
        self.run['videos'] = {'shot_1': {'asset_id': 'video-1'}, 'old_shot': {'asset_id': 'old-video'}}
        self.assertIn('已有 1 段视频会继续复用', dialogue('resume', self.run))
        self.run['pending_review'] = {'gate': 'G3'}
        self.assertIn('先完成当前确认卡', dialogue('resume', self.run))

    def test_review_is_viewer_guidance_not_claimed_content_inspection(self):
        self.run['speech'] = {'line_1': {'asset_id': 'speech-1'}}
        self.run['bgm'] = {'asset_id': 'music-1'}
        message = dialogue('review', self.run, duration=12.234)
        self.assertIn('实测 12.2 秒', message)
        self.assertIn('请播放看看', message)
        self.assertIn('配乐的情绪', message)
        self.assertNotIn('内容检查通过', message)

    def test_reply_keeps_actual_answer_and_does_not_claim_revision(self):
        result = {'kind': 'reply', 'summary': '第二镜安排咪咪收竿，用动作回应前面的等待。'}
        self.assertEqual(dialogue('revision', self.run, result), result['summary'])

    def test_helpers_do_not_mutate_run_or_raise_on_incomplete_legacy_data(self):
        before = deepcopy(self.run)
        for stage in ('received', 'story_review', 'analysis', 'creative', 'assets', 'storyboard',
                      'music_ready', 'video_saved', 'postproduction', 'review', 'resume', 'progress', 'revision', 'delivery'):
            with self.subTest(stage=stage):
                self.assertIsInstance(dialogue(stage, self.run), str)
                self.assertIsInstance(dialogue(stage, {}), str)
        self.assertEqual(self.run, before)


if __name__ == '__main__':
    unittest.main()
