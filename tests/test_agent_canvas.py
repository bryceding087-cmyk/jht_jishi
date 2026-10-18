"""Canvas migration does not need a provider, database, or generated media."""
from copy import deepcopy
import unittest

from agent_canvas import LAYOUT_VERSION, node_id, sync_agent_canvas


class AgentCanvasTests(unittest.TestCase):
    def setUp(self):
        self.run = {'id': 'sample-run', 'artifacts': {}}

    def node(self, key, kind='asset', **fields):
        return {'id': node_id(self.run['id'], key), 'agent_run_id': self.run['id'],
                'agent_key': key, 'agent_layout_pending': True, 'type': kind, 'x': 0, 'y': 0, **fields}

    def test_video_result_merges_without_changing_generation_or_manual_position(self):
        shot = self.node('shot_1', 'video', x=250, y=360,
                         agent_layout={'x': 100, 'y': 160, 'version': 1},
                         settings={'prompt': 'the exact prompt', 'duration': 8, 'resolution': '720p'})
        shot.pop('agent_layout_pending')
        result = self.node('result:shot_1', 'video-asset', assetId='clip-1', mediaWidth=1280, mediaHeight=720,
                           settings={'prompt': 'default prompt', 'duration': 5})
        manual = {'id': 'manual', 'type': 'video', 'x': 1200, 'y': 400, 'settings': {'prompt': 'mine'}}
        graph = {'nodes': [shot, result, manual], 'edges': [
            {'id': 'user', 'from': result['id'], 'to': manual['id']},
            {'id': 'duplicate', 'from': shot['id'], 'to': manual['id']},
            {'id': 'self', 'from': result['id'], 'to': shot['id']},
            {'id': 'flow', 'from': shot['id'], 'to': result['id'], 'relation': 'agent-flow'},
        ]}
        original_settings = deepcopy(shot['settings'])
        sync_agent_canvas(graph, self.run)
        self.assertEqual(len(graph['nodes']), 2)
        self.assertEqual(shot['assetId'], 'clip-1')
        self.assertEqual(shot['settings'], original_settings)
        self.assertEqual((shot['x'], shot['y']), (250, 360))
        self.assertEqual(graph['edges'], [{'id': 'user', 'from': shot['id'], 'to': manual['id']}])
        self.assertEqual(manual, {'id': 'manual', 'type': 'video', 'x': 1200, 'y': 400, 'settings': {'prompt': 'mine'}})
        self.assertFalse(sync_agent_canvas(graph, self.run))

    def test_distinct_or_orphan_result_is_preserved(self):
        shot = self.node('shot_1', 'video', assetId='replacement')
        result = self.node('result:shot_1', 'video-asset', assetId='original')
        orphan = self.node('result:shot_2', 'video-asset', assetId='orphan')
        graph = {'nodes': [shot, result, orphan], 'edges': []}
        sync_agent_canvas(graph, self.run)
        self.assertEqual({n['assetId'] for n in graph['nodes']}, {'replacement', 'original', 'orphan'})

    def test_internal_documents_removed_but_rewrites_and_copies_survive(self):
        self.run['artifacts'] = {'analysis': {'content': '{"duration":8}'},
                                 'creative': {'content': '{"old":true}'}}
        original = self.node('doc:analysis', 'prompt', settings={'prompt': '{"duration":8}'})
        changed = self.node('doc:creative', 'prompt', x=700, y=42, settings={'prompt': '{"my notes":true}'})
        human = self.node('doc:custom', 'prompt', x=800, y=42, settings={'prompt': 'Keep my prose'})
        old_json = self.node('doc:quality', 'prompt', settings={'prompt': '{"passed":true}'})
        old_prose = self.node('doc:draft', 'prompt', settings={'prompt': 'An old story draft'})
        copied = {**deepcopy(original), 'id': 'user-copy'}
        graph = {'nodes': [original, changed, human, old_json, old_prose, copied],
                 'edges': [{'id': 'notes', 'from': changed['id'], 'to': human['id']},
                           {'id': 'internal', 'from': original['id'], 'to': changed['id']}]}
        sync_agent_canvas(graph, self.run, artifact_contents={'draft': ['An old story draft']})
        self.assertEqual({n['id'] for n in graph['nodes']}, {changed['id'], human['id'], copied['id']})
        for node in (changed, human):
            self.assertNotIn('agent_run_id', node)
            self.assertNotIn('agent_key', node)
        self.assertEqual((changed['x'], changed['y']), (700, 42))
        self.assertEqual(graph['edges'], [{'id': 'notes', 'from': changed['id'], 'to': human['id']}])
        self.assertFalse(sync_agent_canvas(graph, self.run))

    def test_character_duplicates_merge_but_old_real_images_remain(self):
        visual = self.node('visual_1', assetId='character-image', settings={'prompt': 'image prompt'})
        character = self.node('character:visual_1', 'character', assetId='character-image',
                              characterId='character-id', characterName='Cat', description='A cat', tone='gentle')
        other = self.node('visual_2', assetId='different-image')
        different = self.node('character:visual_2', 'character', assetId='different-character-image')
        historic = self.node('visual_8', assetId='old-reference', x=970, y=190,
                             agent_layout={'x': 100, 'y': 100, 'version': 1})
        historic.pop('agent_layout_pending')
        shot = self.node('shot_1', 'video')
        graph = {'nodes': [visual, character, other, different, historic, shot], 'edges': [
            {'id': 'character-input', 'from': character['id'], 'to': shot['id']},
            {'id': 'historic-input', 'from': historic['id'], 'to': shot['id']} ]}
        sync_agent_canvas(graph, self.run)
        self.assertEqual(visual['type'], 'character')
        self.assertEqual(visual['characterId'], 'character-id')
        self.assertEqual(visual['settings'], {'prompt': 'image prompt'})
        self.assertNotIn(character, graph['nodes'])
        self.assertIn(different, graph['nodes'])
        self.assertIn(historic, graph['nodes'])
        self.assertEqual((historic['x'], historic['y']), (970, 190))
        self.assertEqual(graph['edges'][0]['from'], visual['id'])
        self.assertFalse(sync_agent_canvas(graph, self.run))

    def test_original_story_merges_only_when_both_texts_match(self):
        self.run.update(source='original', working_story='adopted')
        source = self.node('source', 'prompt', settings={'prompt': 'original'})
        working = self.node('working', 'prompt', settings={'prompt': 'adopted'})
        script = self.node('script', 'script')
        graph = {'nodes': [source, working, script], 'edges': [{'id': 'input', 'from': source['id'], 'to': script['id']}]}
        sync_agent_canvas(graph, self.run)
        self.assertNotIn(source, graph['nodes'])
        self.assertEqual(graph['edges'][0]['from'], working['id'])
        edited = self.node('source', 'prompt', settings={'prompt': 'user edit'})
        graph['nodes'].append(edited)
        sync_agent_canvas(graph, self.run)
        self.assertIn(edited, graph['nodes'])

    def test_large_reference_batch_has_no_stages_or_flow_and_is_stable(self):
        keys = ['source', *[f'visual_{i}' for i in range(1, 9)], 'anchor_1', 'anchor_2', 'anchor_3',
                'script', *[f'shot_{i}' for i in range(1, 9)], 'final']
        graph = {'nodes': [self.node(key) for key in keys], 'edges': []}
        graph['nodes'][0]['agent_stage'] = 0
        self.assertTrue(sync_agent_canvas(graph, self.run))
        references = [n for n in graph['nodes'] if n['agent_key'].startswith(('visual_', 'anchor_'))]
        self.assertLess(max(n['y'] for n in references)-min(n['y'] for n in references), 2000)
        for a in graph['nodes']:
            self.assertNotIn('agent_stage', a)
            self.assertEqual(a['agent_layout']['version'], LAYOUT_VERSION)
            for b in graph['nodes']:
                if a['id'] >= b['id']:
                    continue
                self.assertFalse(a['x'] < b['x']+440 and a['x']+440 > b['x'] and a['y'] < b['y']+340 and a['y']+340 > b['y'])
        self.assertEqual(graph['edges'], [])
        saved = deepcopy(graph)
        self.assertFalse(sync_agent_canvas(graph, self.run))
        self.assertEqual(graph, saved)
        by_key = {n['agent_key']: n for n in graph['nodes']}
        self.assertGreater(by_key['script']['x'], max(n['x']+440 for n in references))

    def test_new_layout_skips_empty_columns_and_avoids_user_obstacles(self):
        shot = self.node('shot_1', 'video')
        user = {'id': 'user', 'type': 'prompt', 'x': 100, 'y': 160}
        unrelated = {'id': 'other-run', 'agent_run_id': 'other', 'type': 'prompt', 'x': 2000, 'y': 160}
        graph = {'nodes': [shot, user, unrelated], 'edges': [
            {'id': 'manual', 'from': user['id'], 'to': unrelated['id']},
            {'id': 'other-flow', 'from': user['id'], 'to': unrelated['id'], 'relation': 'agent-flow', 'agent_run_id': 'other'},
        ]}
        original_edges = deepcopy(graph['edges'])
        sync_agent_canvas(graph, self.run)
        self.assertEqual(shot['x'], 100)
        self.assertGreaterEqual(shot['y'], 560)
        self.assertEqual((user['x'], user['y']), (100, 160))
        self.assertEqual(graph['edges'], original_edges)

    def test_references_arriving_one_by_one_stay_compact(self):
        graph = {'nodes': [self.node('source', 'prompt')], 'edges': []}
        sync_agent_canvas(graph, self.run)
        for number in range(1, 13):
            graph['nodes'].append(self.node(f'visual_{number}', assetId=f'image-{number}'))
            sync_agent_canvas(graph, self.run)
        references = [node for node in graph['nodes'] if node['type'] == 'asset']
        self.assertEqual(len({node['x'] for node in references}), 3)
        self.assertLess(max(node['y'] for node in references)-min(node['y'] for node in references), 2000)
        self.assertFalse(sync_agent_canvas(graph, self.run))


if __name__ == '__main__':
    unittest.main()
