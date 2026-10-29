"""Source normalization and model-aware cuts, without paid provider requests."""
from copy import deepcopy
import unittest

from fastapi import HTTPException
from provider_adapters import video_capabilities
from studio_graph import validate_extraction, plan_video_clips, clip_storyboard
from test_studio_extraction_references import SCRIPT, catalog, shots
from test_studio_clip_planning import shot


class StoryboardPlanningTests(unittest.TestCase):
    def test_valid_line_serializations_are_canonical_and_keep_source_evidence(self):
        for raw in ('10', '10-10', '第10行', '１０－１０', [10], [10, 10]):
            with self.subTest(raw=raw):
                value={'assets': [], 'shots': [dict(shots()[1], sourceLine=raw)]}
                before=deepcopy(value)
                result=validate_extraction(value,SCRIPT,5,catalog(),scene_ids=['5-2'],frozen_catalog=True)
                self.assertEqual(result['shots'][0]['sourceLine'],10)
                self.assertEqual(result['shots'][0]['sources'][0]['quote'],'△小夫放下安全帽。')
                self.assertEqual(value,before)

    def test_line_spans_survive_revalidation_without_inventing_evidence(self):
        result=validate_extraction({'assets':[],'shots':[dict(shots()[1],sourceLine='10–11')]},SCRIPT,5,catalog(),scene_ids=['5-2'])
        item=result['shots'][0]
        self.assertEqual(item['sourceLineEnd'],11)
        self.assertEqual(item['sources'][0]['quote'],'△小夫放下安全帽。\n小夫：欢迎回家。')
        again=validate_extraction({'assets':[],'shots':[item]},SCRIPT,5,catalog(),scene_ids=['5-2'])
        self.assertEqual(again['shots'][0]['sources'],item['sources'])
        for raw in ('6-10','10-999','11-10','10或11',True,[True,10],{'line':10}):
            with self.subTest(raw=raw),self.assertRaises(HTTPException):
                validate_extraction({'assets':[],'shots':[dict(shots()[1],sourceLine=raw)]},SCRIPT,5,catalog(),scene_ids=['5-2'])

    def test_minimax_combines_five_and_six_seconds_across_normal_camera_cut(self):
        caps=video_capabilities({'provider_id':'aimarket','model_id':'minimax-h3','capabilities':{}})
        original=[shot('one',5),shot('two',6,cutBefore=True)]
        clips=plan_video_clips(original,caps)
        self.assertEqual(len(clips),1)
        self.assertEqual(clips[0]['seconds'],11)
        self.assertEqual(clips[0]['sourceShots'],original)
        for text in ('0–5 秒','5–11 秒','one：你好。','two：你好。','切换至下一机位'):
            self.assertIn(text,clip_storyboard(clips[0]))

    def test_structured_dialogue_is_lossless_and_still_checks_script_coverage(self):
        structured=[{'speaker':'小夫','text':'欢迎回家。','offscreen':True,'line':11}]
        value={'assets':[],'shots':[dict(shots()[1],sourceLine='10-11',dialogue=structured)]}
        result=validate_extraction(value,SCRIPT,5,catalog(),scene_ids=['5-2'])
        self.assertEqual(result['shots'][0]['dialogue'],'小夫（画外音）：欢迎回家。')
        self.assertEqual(result['shots'][0]['dialogueSegments'],structured)
        for raw in ([],None,[{'text':'不同的台词'}],[{'speaker':'小夫'}]):
            with self.subTest(raw=raw),self.assertRaises(HTTPException):
                validate_extraction({'assets':[],'shots':[dict(shots()[1],dialogue=raw)]},SCRIPT,5,catalog(),scene_ids=['5-2'])

    def test_model_limits_and_true_generation_breaks_remain_separate(self):
        caps={'durations':list(range(4,16)),'multi_shot':True}
        for second in [shot('two',11,cutBefore=True),shot('two',6,generationBreakBefore=True),dict(shot('two',6),sceneKey='another')]:
            self.assertEqual(len(plan_video_clips([shot('one',5),second],caps)),2)
        catalog=[{'key':'hero','name':'小林','type':'character','variant':'日常'},
                 {'key':'hero-night','name':'小林','type':'character','variant':'晚礼服'}]
        changed=dict(shot('two',6),references=['hero-night','scene'])
        self.assertEqual(len(plan_video_clips([shot('one',5),changed],caps,asset_catalog=catalog)),2)
        self.assertEqual(len(plan_video_clips([shot('one',5),shot('two',6,cutBefore=True)],{'durations':[6,12]})),2)


if __name__=='__main__':unittest.main()
