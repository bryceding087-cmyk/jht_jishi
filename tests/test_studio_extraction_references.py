"""Asset registration and scene extraction contracts without model/network calls."""
from copy import deepcopy
import unittest

from fastapi import HTTPException
from studio_graph import validate_asset_catalog, validate_extraction


SCRIPT = '''# 第5集《交付》
### 场5-1
日 外 门前
出场人物：小夫
△小夫扶住安全帽。
小夫：终于交付了。
### 场5-2
日 内 客厅
出场人物：小夫
△小夫放下安全帽。
小夫：欢迎回家。
'''


def catalog():
    return [dict(key=key, type=kind, name=name, variant=variant,
                 facts='既定原文事实', design='前四集既定设计', anchors=['既定特征'],
                 sources=[{'episode': 1, 'scene': '1-1', 'line': 3, 'quote': '原始出场'}], episodes=[1])
            for key, kind, name, variant in [('c01', 'character', '小夫', '施工日'),
                                             ('s01', 'scene', '门前', ''),
                                             ('p01', 'prop', '安全帽', '施工日')]]


def raw(key='c11', **extra):
    return dict(key=key, type='character', name='小夫', variant='施工日',
                facts='新回复中的事实', design='新回复中的设计', anchors=['新特征'],
                sourceScene='5-1', sourceLine=5, **extra)


def shots():
    return [dict(sceneId=sid, sourceLine=line, sceneKey='s01', characterKeys=['c01'], propKeys=['p01'],
                 title='交付', seconds=4, framing='中景', staging='门前', startState='扶住安全帽',
                 action='小夫说话', endState='微笑', camera='固定', lighting='日光', sound='环境声',
                 dialogue=dialogue, continuity='保持衣着')
            for sid, line, dialogue in [('5-1', 5, '终于交付了。'), ('5-2', 10, '欢迎回家。')]]


class StudioExtractionReferenceTests(unittest.TestCase):
    def test_same_variant_reuse_maps_new_key_and_persists_alias(self):
        assets = catalog(); before = deepcopy(assets)
        value = {'assets': [raw(reuseId='c01')], 'shots': shots()}
        value['shots'][0]['characterKeys'] = ['c11']
        result = validate_extraction(value, SCRIPT, 5, assets)
        self.assertEqual(result['shots'][0]['characterKeys'], ['c01'])
        self.assertEqual(result['shots'][0]['references'], ['s01', 'c01', 'p01'])
        self.assertEqual(len(result['assets']), 3)
        self.assertIn('c11', result['assets'][0]['aliases'])
        self.assertEqual(result['assets'][0]['design'], before[0]['design'])
        self.assertEqual(assets, before)
        # Registration aliases survive a later independent scene request.
        registered = validate_asset_catalog({'assets': [raw(reuseId='c01')]}, SCRIPT, 5, assets)
        frozen = validate_extraction({'assets': [], 'shots': value['shots']}, SCRIPT, 5,
                                     registered['assets'], frozen_catalog=True)
        self.assertEqual(frozen['shots'][0]['characterKeys'], ['c01'])

    def test_real_failure_new_variant_with_reuse_id_keeps_new_identity(self):
        assets = catalog(); before = deepcopy(assets)
        character = raw(reuseId='c01')
        character['variant'] = '交付日（白帽旧划痕·价签撕除留白印）'
        helmet = {**character, 'key': 'p30', 'reuseId': 'p01', 'name': '安全帽', 'type': 'prop'}
        value = {'assets': [character, helmet], 'shots': shots()}
        value['shots'][0].update(characterKeys=['c11'], propKeys=['p30'])
        result = validate_extraction(value, SCRIPT, 5, assets)
        by_key = {item['key']: item for item in result['assets']}
        self.assertEqual(len(by_key), 5)
        self.assertEqual(by_key['c01'], before[0])
        self.assertEqual(by_key['p01'], before[2])
        self.assertEqual(by_key['c11']['variant'], character['variant'])
        self.assertEqual(by_key['c11']['baseKey'], 'c01')
        self.assertEqual(by_key['p30']['baseKey'], 'p01')
        self.assertEqual(result['shots'][0]['references'], ['s01', 'c11', 'p30'])
        self.assertEqual(result['shots'][1]['references'], ['s01', 'c01', 'p01'])
        self.assertEqual(assets, before)

    def test_new_variant_cannot_overwrite_old_key(self):
        changed = raw('c01', reuseId='c01'); changed['variant'] = '交付日'
        with self.assertRaises(HTTPException) as error:
            validate_asset_catalog({'assets': [changed]}, SCRIPT, 5, catalog())
        self.assertIn('新编号', error.exception.detail)

    def test_same_identity_deduplicates_and_different_variants_stay_separate(self):
        registered = validate_asset_catalog({'assets': [raw()]}, SCRIPT, 5, catalog())
        self.assertEqual(len(registered['assets']), 3)
        self.assertEqual(registered['aliases']['c11'], 'c01')
        different = raw('c12'); different['variant'] = '雨衣'
        result = validate_asset_catalog({'assets': [different]}, SCRIPT, 5, registered['assets'])
        self.assertEqual(len(result['assets']), 4)
        self.assertEqual(result['assets'][-1]['key'], 'c12')

    def test_unique_names_and_typographic_key_normalization(self):
        value = {'assets': [], 'shots': shots()}
        value['shots'][0].update(sceneKey='门前', characterKeys=['小夫'], propKeys=[' ｐ０１ '])
        result = validate_extraction(value, SCRIPT, 5, catalog())
        self.assertEqual(result['shots'][0]['references'], ['s01', 'c01', 'p01'])

    def test_ambiguous_name_is_not_guessed(self):
        assets = catalog(); assets.append({**assets[0], 'key': 'c02', 'variant': '交付日'})
        value = {'assets': [], 'shots': shots()}; value['shots'][0]['characterKeys'] = ['小夫']
        with self.assertRaises(HTTPException) as error:
            validate_extraction(value, SCRIPT, 5, assets)
        for text in ('场 5-1', '第 1 镜', 'characterKeys=小夫', 'c01=小夫', 'c02=小夫'):
            self.assertIn(text, error.exception.detail)

    def test_unknown_and_wrong_type_refs_are_precise_and_never_dropped(self):
        for reference in ('c99', 'p01', ['c01']):
            with self.subTest(reference=reference):
                value = {'assets': [], 'shots': shots()}; value['shots'][1]['characterKeys'] = [reference]
                original = deepcopy(value)
                with self.assertRaises(HTTPException) as error:
                    validate_extraction(value, SCRIPT, 5, catalog())
                self.assertIn('场 5-2', error.exception.detail)
                self.assertIn('characterKeys=', error.exception.detail)
                self.assertIn('c01=小夫', error.exception.detail)
                self.assertEqual(value, original)

    def test_duplicate_alias_and_real_key_reference_requires_split(self):
        value = {'assets': [raw(reuseId='c01')], 'shots': shots()}
        value['shots'][0]['characterKeys'] = ['c01', 'c11']
        with self.assertRaises(HTTPException) as error:
            validate_extraction(value, SCRIPT, 5, catalog())
        self.assertIn('参考重复', error.exception.detail)

    def test_scene_subset_uses_global_lines_and_checks_only_its_dialogue(self):
        result = validate_extraction({'assets': [], 'shots': [shots()[1]]}, SCRIPT, 5,
                                     catalog(), scene_ids=['5-2'], frozen_catalog=True)
        self.assertEqual(result['shots'][0]['sources'][0]['line'], 10)
        self.assertEqual(result['shots'][0]['sources'][0]['quote'], '△小夫放下安全帽。')
        wrong = shots()[1]; wrong['sourceLine'] = 4
        with self.assertRaises(HTTPException):
            validate_extraction({'assets': [], 'shots': [wrong]}, SCRIPT, 5,
                                catalog(), scene_ids=['5-2'], frozen_catalog=True)
        missing_dialogue = shots()[1]; missing_dialogue['dialogue'] = ''
        with self.assertRaises(HTTPException) as error:
            validate_extraction({'assets': [], 'shots': [missing_dialogue]}, SCRIPT, 5,
                                catalog(), scene_ids=['5-2'], frozen_catalog=True)
        self.assertIn('对白遗漏', error.exception.detail)

    def test_scene_subset_rejects_unrelated_shots_and_missing_coverage(self):
        for value, scene_ids in [({'assets': [], 'shots': shots()}, ['5-2']),
                                 ({'assets': [], 'shots': [shots()[0]]}, ['5-1', '5-2']),
                                 ({'assets': [], 'shots': shots()}, ['5-9'])]:
            with self.subTest(scene_ids=scene_ids), self.assertRaises(HTTPException):
                validate_extraction(value, SCRIPT, 5, catalog(), scene_ids=scene_ids)

    def test_frozen_catalog_accepts_alias_without_modifying_any_definition(self):
        assets = catalog(); before = deepcopy(assets)
        value = {'assets': [raw(reuseId='c01')], 'shots': shots()}
        value['shots'][0]['characterKeys'] = ['c11']
        result = validate_extraction(value, SCRIPT, 5, assets, frozen_catalog=True)
        self.assertEqual(result['assets'], before)
        self.assertEqual(result['shots'][0]['characterKeys'], ['c01'])
        value['assets'][0]['variant'] = '交付日'
        with self.assertRaises(HTTPException) as error:
            validate_extraction(value, SCRIPT, 5, assets, frozen_catalog=True)
        self.assertIn('先登记资产', error.exception.detail)

    def test_reuse_never_renames_identity_or_creates_missing_target(self):
        for entry in [dict(raw(reuseId='c01'), name='老周'), raw(reuseId='c99')]:
            with self.subTest(entry=entry), self.assertRaises(HTTPException):
                validate_asset_catalog({'assets': [entry]}, SCRIPT, 5, catalog())

    def test_frozen_usage_lists_reuse_definitions_without_inventing_source_locations(self):
        assets = catalog(); before = deepcopy(assets)
        for usage in [['s01', 'c01', 'p01'],
                      [{k: a[k] for k in ('key', 'name', 'type', 'variant')} | {'usedInShots': [1]} for a in assets],
                      ['s01', {'key': 'c01'}, {'reuseId': 'p01'}]]:
            with self.subTest(usage=usage):
                value = {'assets': usage, 'shots': shots()}; original = deepcopy(value)
                result = validate_extraction(value, SCRIPT, 5, assets, frozen_catalog=True)
                self.assertEqual(result['assets'], before)
                self.assertEqual(result['shots'][0]['references'], ['s01', 'c01', 'p01'])
                self.assertEqual(result['shots'][0]['sources'][0]['line'], 5)
                self.assertEqual(assets, before)
                self.assertEqual(value, original)

    def test_frozen_usage_lists_still_reject_unknown_or_conflicting_identity(self):
        for item in ['c99', {'key': 'c99'}, {'key': 'c01', 'name': '老周'},
                     {'key': 'c01', 'type': 'prop'}, {'key': 'c01', 'variant': '交付日'},
                     {'key': 'c01', 'reuseId': 'p01'}, {'key': 'c01', 'name': None}]:
            with self.subTest(item=item), self.assertRaises(HTTPException):
                validate_extraction({'assets': [item], 'shots': shots()}, SCRIPT, 5, catalog(), frozen_catalog=True)
        ambiguous = catalog() + [{**catalog()[0], 'key': 'c02', 'variant': '交付日'}]
        with self.assertRaises(HTTPException):
            validate_extraction({'assets': ['小夫'], 'shots': shots()}, SCRIPT, 5, ambiguous, frozen_catalog=True)

    def test_usage_summary_support_does_not_relax_real_source_or_dialogue_checks(self):
        for item in ['c01', {'key': 'c01', 'name': '小夫', 'type': 'character'}]:
            with self.subTest(item=item), self.assertRaises(HTTPException):
                validate_asset_catalog({'assets': [item]}, SCRIPT, 5, catalog())
        for item in [dict(raw('c01'), sourceLine=999),
                     {'key': 'c01', 'type': 'character', 'name': '小夫', 'design': '未注明出处的新设计'}]:
            with self.subTest(item=item), self.assertRaises(HTTPException):
                validate_extraction({'assets': [item], 'shots': shots()}, SCRIPT, 5, catalog(), frozen_catalog=True)
        for change in [{'sourceLine': 999}, {'dialogue': ''}, {'characterKeys': ['c99']}]:
            value = {'assets': ['c01'], 'shots': shots()}; value['shots'][0].update(change)
            with self.subTest(change=change), self.assertRaises(HTTPException):
                validate_extraction(value, SCRIPT, 5, catalog(), frozen_catalog=True)


if __name__ == '__main__':
    unittest.main()
