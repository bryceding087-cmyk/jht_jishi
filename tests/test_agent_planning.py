"""Pure planning regressions; no application data or provider calls."""

from copy import deepcopy
import unittest

from fastapi import HTTPException

from agent_planning import CONTINUATION_NOTE, normalize_storyboard


def scene(duration=4, **fields):
    return {'id': 'shot_1', 'duration': duration, 'prompt': '人物走到窗前，看向远方。',
            'description': '走到窗前', 'line_ids': ['line_1'], 'visual_ids': ['visual_1'], **fields}


class StoryboardPlanningTests(unittest.TestCase):
    def test_duration_coercion_rounds_up_to_supported_values_without_mutation(self):
        original = {'title': '故事', 'scenes': [scene('5.2')]}
        saved = deepcopy(original)
        result, notes = normalize_storyboard(original, [4, 6, 10])
        self.assertEqual(result['scenes'][0]['duration'], 6)
        self.assertEqual(result['total_duration'], 6)
        self.assertEqual(original, saved)
        self.assertTrue(notes)
        for value in (None, False, 'unknown', float('nan'), float('inf'), -1):
            with self.subTest(value=value):
                result, _ = normalize_storyboard({'scenes': [scene(value)]}, [4, 6])
                self.assertEqual(result['scenes'][0]['duration'], 4)

    def test_long_shot_prefers_smallest_total_then_fewest_clips(self):
        result, notes = normalize_storyboard({'scenes': [scene(25)]}, [4, 8, 12])
        clips = result['scenes']
        self.assertEqual([clip['duration'] for clip in clips], [12, 12, 4])
        self.assertEqual([clip['id'] for clip in clips], ['shot_1', 'shot_1_part_2', 'shot_1_part_3'])
        self.assertEqual([clip['line_ids'] for clip in clips], [['line_1'], [], []])
        self.assertEqual(clips[1]['continuation_of'], 'shot_1')
        self.assertIn(CONTINUATION_NOTE, clips[1]['prompt'])
        self.assertIn('人物走到窗前，看向远方。', clips[1]['prompt'])
        self.assertEqual(clips[2]['visual_ids'], ['visual_1'])
        self.assertTrue(notes)

    def test_sparse_profile_uses_exact_total_when_more_clips_are_needed(self):
        result, _ = normalize_storyboard({'scenes': [scene(12)]}, [4, 10])
        self.assertEqual([clip['duration'] for clip in result['scenes']], [4, 4, 4])
        continuous, _ = normalize_storyboard({'scenes': [scene(17)]}, list(range(4, 16)))
        self.assertEqual([clip['duration'] for clip in continuous['scenes']], [13, 4])

    def test_timing_repair_and_continuation_are_idempotent(self):
        result, _ = normalize_storyboard({'scenes': [scene(26)]}, [4, 8, 12])
        repeated, notes = normalize_storyboard(result, [4, 8, 12])
        self.assertEqual(repeated, result)
        self.assertEqual(notes, [])

    def test_speech_extends_root_and_spans_continuations_without_duplicating_line(self):
        result, _ = normalize_storyboard({'scenes': [scene(4)]}, [4, 8, 12], {'line_1': 19.2})
        self.assertEqual([clip['duration'] for clip in result['scenes']], [12, 8])
        self.assertEqual(result['total_duration'], 20)
        self.assertEqual([lid for clip in result['scenes'] for lid in clip['line_ids']], ['line_1'])
        repeated, notes = normalize_storyboard(result, [4, 8, 12], {'line_1': 19.2})
        self.assertEqual(repeated, result)
        self.assertEqual(notes, [])

    def test_existing_continuation_grows_without_shortening_planned_segments(self):
        story = {'scenes': [scene(12), scene(4, id='shot_1_part_2', continuation_of='shot_1', line_ids=[])]}
        result, _ = normalize_storyboard(story, [4, 8, 12], {'line_1': 20})
        self.assertEqual([clip['duration'] for clip in result['scenes']], [12, 8])
        self.assertEqual(result['scenes'][1]['id'], 'shot_1_part_2')

    def test_fractional_speech_times_are_summed_before_rounding(self):
        story = {'scenes': [scene(4, line_ids=['line_1', 'line_2', 'line_3'])]}
        result, notes = normalize_storyboard(story, [4, 8], {lid: 1.2 for lid in story['scenes'][0]['line_ids']})
        self.assertEqual(result['total_duration'], 4)
        self.assertEqual(notes, [])

    def test_reused_video_keeps_non_model_duration_and_speech_gets_new_footage(self):
        original = {'scenes': [scene('7.1', video_asset_id='provided-video')]}
        result, _ = normalize_storyboard(original, [4, 6], {'line_1': 12})
        self.assertEqual([clip['duration'] for clip in result['scenes']], [8, 4])
        self.assertEqual(result['scenes'][0]['video_asset_id'], 'provided-video')
        self.assertNotIn('video_asset_id', result['scenes'][1])
        self.assertEqual(result['scenes'][0]['line_ids'], ['line_1'])
        repeated, notes = normalize_storyboard(result, [4, 6], {'line_1': 12})
        self.assertEqual(repeated, result)
        self.assertEqual(notes, [])

    def test_total_time_and_clip_count_limits_are_enforced(self):
        for story, profile in [({'scenes': [scene(1201)]}, [4, 15]),
                               ({'scenes': [scene(325)]}, [4]),
                               ({'scenes': [scene(60, id=f'shot_{i}') for i in range(21)]}, [60])]:
            with self.subTest(story=story), self.assertRaises(HTTPException):
                normalize_storyboard(story, profile)
        with self.assertRaises(HTTPException):
            normalize_storyboard({'scenes': [scene()]}, [])


if __name__ == '__main__':
    unittest.main()
