"""Direct material references and safe migration of old automatic frame graphs."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from agent_runtime import Preferences
from studio_graph import build_graph, digest, merge_graph, node_id, resolve_inputs
from studio_prompt_optimizer import apply_direction, mark_merged_optimizations


def run_fixture(mode='first-last'):
    assets = [dict(key=key, type=kind, name=name, variant=variant, facts='原文事实',
                   design='明确的既定设计', anchors=['稳定特征'], sources=[])
              for key, kind, name, variant in [('hero', 'character', '小林', '默认'),
                  ('coat', 'character', '小林', '雨衣'), ('scene', 'scene', '车站', ''),
                  ('letter', 'prop', '旧信', ''), ('unused', 'prop', '未出镜的水杯', '')]]
    shot = dict(key='ep1-shot1', title='查看地址', seconds=4, sceneId='1-1', sceneKey='scene',
                characterKeys=['coat'], propKeys=['letter'], references=['scene', 'coat', 'letter'],
                framing='中景', staging='车站门前', startState='握住信封', action='查看地址', endState='抬头',
                camera='缓慢推进', lighting='柔和日光', sound='环境声', dialogue='我回来了。', continuity='保持持物状态', sources=[])
    return {'id': 'direct-test', 'title': '归途', 'preferences': {'aspect_ratio': '16:9', 'models': {}, 'studio_video_mode': mode},
            'studio': {'brief': {'episode_count': 1}, 'catalog': assets,
                       'documents': {'episode-1': {'status': 'current', 'content': '测试剧本', 'version': 1}},
                       'extractions': {'1': {'source_digest': digest('测试剧本'), 'shots': [shot]}}}}


def managed_edge(source, target, slot='', order=1):
    return {'from': source, 'to': target, 'relation': 'reference', 'slot': slot, 'order': order,
            'role': '已有参考', 'mujian': True, 'studio_run_id': 'direct-test', 'hidden': True}


def legacy_graph(run):
    graph = build_graph(run)
    video = next(node for node in graph['nodes'] if node['type'] == 'video')
    material_inputs = deepcopy(video['mujian']['inputs'])
    frames = []
    for suffix, role, label in [('first', 'firstFrame', '首帧'), ('last', 'lastFrame', '尾帧')]:
        key = video['mujian']['key'] + ':' + suffix
        inputs = deepcopy(material_inputs)
        if suffix == 'last':
            inputs.insert(0, {'nodeId': frames[0]['id'], 'kind': 'image', 'role': '首帧连续性', 'slot': ''})
        frame = {'id': node_id(run['id'], key), 'type': 'asset', 'title': f'第 1 集片段 01 · {label}',
                 'x': 900, 'y': 100 if suffix == 'first' else 500,
                 'image_settings': {'prompt': label + '静态画面', 'aspect_ratio': '16:9', 'model_config_id': '',
                     'size': '2K', 'count': 1, 'watermark': False, 'reference_asset_ids': [], 'visual_style': None, 'mentions': []},
                 'mujian': {'runId': run['id'], 'role': role, 'key': key, 'inputs': inputs, 'episode': 1, 'sceneKey': 'scene', 'sources': []}}
        frames.append(frame)
    graph['nodes'].extend(frames)
    graph['edges'] = [edge for edge in graph['edges'] if edge['to'] != video['id']]
    for frame in frames:
        graph['edges'].extend(managed_edge(ref['nodeId'], frame['id'], order=i + 1)
                              for i, ref in enumerate(frame['mujian']['inputs']))
    video['mujian'].update(mode='first-last', inputs=[
        {'nodeId': frame['id'], 'kind': 'image', 'role': frame['title'], 'slot': slot}
        for frame, slot in zip(frames, ('first', 'last'))])
    video['settings'].update(prompt='以既定首帧起步，连续过渡到尾帧。', video_mode='first-last', mentions=[
        {'token': '@' + frame['title'], 'node_id': frame['id'], 'asset_id': '', 'name': frame['title'], 'kind': 'image'} for frame in frames])
    graph['edges'].extend(managed_edge(ref['nodeId'], video['id'], ref['slot'], i + 1)
                          for i, ref in enumerate(video['mujian']['inputs']))
    graph = merge_graph(graph, None, run['id'])
    for node in graph['nodes']:
        node['mujian'].pop('appliedDefinitionHash', None)  # Older saved graphs only had definitionHash.
    return graph


def browser_save_defaults(graph):
    # Exactly the public/app.js normaliseNodes defaults; existing values win.
    defaults = {'prompt': '', 'negative_prompt': '模糊、抖动、人物变形、字幕、水印', 'mode': 'text-to-video',
                'model_config_id': 'default-model', 'aspect_ratio': '16:9', 'duration': 4, 'resolution': '768p', 'asset_ids': []}
    for node in graph['nodes']:
        node['settings'] = {**defaults, **node.get('settings', {})}


class StudioDirectReferenceTests(unittest.TestCase):
    def test_default_and_saved_legacy_modes_all_build_direct_references(self):
        self.assertEqual(Preferences().studio_video_mode, 'reference')
        for mode in ('first', 'first-last', 'reference', 'text'):
            with self.subTest(mode=mode):
                run = run_fixture(mode)
                graph = build_graph(run)
                self.assertFalse(any(node['mujian']['role'] in ('firstFrame', 'lastFrame') for node in graph['nodes']))
                video = next(node for node in graph['nodes'] if node['type'] == 'video')
                expected = [node_id(run['id'], 'asset:' + key) for key in ('scene', 'coat', 'letter')]
                self.assertEqual([ref['nodeId'] for ref in video['mujian']['inputs']], expected)
                self.assertEqual([mention['node_id'] for mention in video['settings']['mentions']], expected)
                self.assertEqual(video['mujian']['mode'], 'reference')
                self.assertEqual(video['settings']['video_mode'], 'auto')
                self.assertNotIn('首帧', video['settings']['prompt'])
                self.assertNotIn('尾帧', video['settings']['prompt'])
                coat = next(node for node in graph['nodes'] if node['mujian'].get('definitionId') == 'coat')
                self.assertEqual(coat['mujian']['inputs'][0]['nodeId'], node_id(run['id'], 'asset:hero'))
                self.assertNotIn(node_id(run['id'], 'asset:unused'), expected)

    def test_clean_browser_saved_legacy_graph_migrates_and_drops_unused_frames(self):
        run = run_fixture(); old = legacy_graph(run)
        browser_save_defaults(old)
        video = next(node for node in old['nodes'] if node['type'] == 'video')
        video.update(x=1234, y=456)
        merged = build_graph(run, old)
        self.assertFalse(any(node['mujian']['role'] in ('firstFrame', 'lastFrame') for node in merged['nodes']))
        new = next(node for node in merged['nodes'] if node['id'] == video['id'])
        self.assertEqual(new['mujian']['mode'], 'reference')
        self.assertEqual((new['x'], new['y']), (1234, 456))
        self.assertFalse(new['mujian'].get('needsReview'))
        self.assertNotEqual(new['settings']['prompt'], video['settings']['prompt'])
        ids = {node['id'] for node in merged['nodes']}
        self.assertTrue(all(edge['from'] in ids and edge['to'] in ids for edge in merged['edges']))
        self.assertTrue(all(set(group['node_ids']).issubset(ids) for group in merged['groups']))

    def test_hand_edited_video_keeps_matching_old_prompt_inputs_and_edges(self):
        for field, value in [('prompt', '用户手动镜头构图'), ('duration', 7), ('model_config_id', 'chosen-model'), ('custom_parameter', 'user')]:
            with self.subTest(field=field):
                run = run_fixture(); old = legacy_graph(run); browser_save_defaults(old)
                video = next(node for node in old['nodes'] if node['type'] == 'video')
                video['settings'][field] = value
                settings, inputs = deepcopy(video['settings']), deepcopy(video['mujian']['inputs'])
                merged = build_graph(run, old)
                saved = next(node for node in merged['nodes'] if node['id'] == video['id'])
                self.assertEqual(saved['settings'], settings)
                self.assertEqual(saved['mujian']['inputs'], inputs)
                self.assertEqual(saved['mujian']['mode'], 'first-last')
                self.assertTrue(saved['mujian']['needsReview'])
                incoming = {edge['from'] for edge in merged['edges'] if edge['to'] == saved['id']}
                self.assertEqual(incoming, {ref['nodeId'] for ref in inputs})
                proposal = saved['mujian']['proposed']
                self.assertEqual(proposal['mujian']['mode'], 'reference')
                self.assertEqual(proposal['settings']['video_mode'], 'auto')
                self.assertEqual({edge['from'] for edge in proposal['incomingEdges']},
                                 {ref['nodeId'] for ref in proposal['mujian']['inputs']})
                self.assertTrue(all(not node['mujian'].get('archived') for node in merged['nodes']
                                    if node['mujian']['role'] in ('firstFrame', 'lastFrame')))

    def test_generated_video_and_required_frame_media_remain_inspectable(self):
        run = run_fixture(); old = legacy_graph(run)
        video = next(node for node in old['nodes'] if node['type'] == 'video')
        video.update(assetId='existing-video', assetUrl='/uploads/existing-video.mp4')
        for index, frame in enumerate(node for node in old['nodes'] if node['mujian']['role'] in ('firstFrame', 'lastFrame')):
            frame.update(assetId=f'frame-{index}', assetUrl=f'/uploads/frame-{index}.png')
        merged = build_graph(run, old)
        saved = next(node for node in merged['nodes'] if node['id'] == video['id'])
        self.assertEqual(saved['assetId'], 'existing-video')
        self.assertEqual(saved['settings'], video['settings'])
        self.assertTrue(saved['mujian']['mediaStale'])
        # After the explicit "keep current" choice the legacy references still
        # resolve; migration must not archive their source frames prematurely.
        saved['mujian'].update(needsReview=False, mediaStale=False)
        host = SimpleNamespace(require_live_asset=lambda aid: {'id': aid, 'kind': 'image'},
                               video_graph_asset_ids=lambda graph, node: ['frame-0', 'frame-1'])
        self.assertEqual(resolve_inputs(host, merged, saved), ['frame-0', 'frame-1'])

    def test_changed_or_rendered_unused_frames_are_archived_not_deleted(self):
        for change in ('prompt', 'media', 'title'):
            with self.subTest(change=change):
                run = run_fixture(); old = legacy_graph(run)
                frame = next(node for node in old['nodes'] if node['mujian']['role'] == 'firstFrame')
                if change == 'prompt': frame['image_settings']['prompt'] = '用户保存的画面构图'
                elif change == 'title': frame['title'] = '用户自定义画面'
                else: frame.update(assetId='saved-picture', assetUrl='/uploads/saved-picture.png')
                merged = build_graph(run, old)
                retained = next(node for node in merged['nodes'] if node['id'] == frame['id'])
                self.assertTrue(retained['mujian']['archived'])
                self.assertEqual(retained['image_settings'], frame['image_settings'])
                self.assertEqual(retained.get('assetId'), frame.get('assetId'))
                video = next(node for node in merged['nodes'] if node['type'] == 'video')
                self.assertEqual(video['mujian']['mode'], 'reference')
                self.assertNotIn(frame['id'], {ref['nodeId'] for ref in video['mujian']['inputs']})

    def test_manual_reference_prevents_silent_migration(self):
        run = run_fixture(); old = legacy_graph(run)
        video = next(node for node in old['nodes'] if node['type'] == 'video')
        old['nodes'].append({'id': 'manual', 'type': 'asset', 'title': '手动参考', 'image_settings': {'prompt': '手动'}})
        edge = {'from': 'manual', 'to': video['id'], 'relation': 'reference'}
        old['edges'].append(edge)
        merged = build_graph(run, old)
        saved = next(node for node in merged['nodes'] if node['id'] == video['id'])
        self.assertEqual(saved['mujian']['mode'], 'first-last')
        self.assertTrue(saved['mujian']['needsReview'])
        self.assertIn(edge, merged['edges'])

    def test_edited_managed_edge_is_not_mistaken_for_untouched_generated_node(self):
        run = run_fixture(); old = legacy_graph(run)
        video = next(node for node in old['nodes'] if node['type'] == 'video')
        incoming = next(edge for edge in old['edges'] if edge['to'] == video['id'])
        incoming['from'] = node_id(run['id'], 'asset:letter')
        merged = build_graph(run, old)
        saved = next(node for node in merged['nodes'] if node['id'] == video['id'])
        self.assertEqual(saved['mujian']['mode'], 'first-last')
        self.assertTrue(saved['mujian']['needsReview'])
        self.assertIn(incoming, merged['edges'])

    def test_new_optimization_does_not_drop_proposal_reference_metadata(self):
        run = run_fixture(); old = legacy_graph(run)
        video = next(node for node in old['nodes'] if node['type'] == 'video')
        video['settings']['prompt'] = '用户旧方案'
        fresh = build_graph(run)
        intended = next(node for node in fresh['nodes'] if node['type'] == 'video')
        apply_direction(intended, '保持既定角色和道具归属，沿原有动作顺序连续呈现。', {'skills': ['expert']}, 'new-guidance')
        proposal = deepcopy(fresh)
        intended = next(node for node in proposal['nodes'] if node['type'] == 'video')
        merged = merge_graph(fresh, old, run['id'])
        mark_merged_optimizations(merged, proposal, old)
        saved = next(node for node in merged['nodes'] if node['id'] == video['id'])
        update = saved['mujian']['proposed']
        self.assertEqual(saved['mujian']['promptOptimization']['status'], 'proposed')
        self.assertEqual(update['mujian']['mode'], 'reference')
        self.assertEqual(update['mujian']['inputs'], intended['mujian']['inputs'])
        self.assertEqual({edge['from'] for edge in update['incomingEdges']},
                         {ref['nodeId'] for ref in intended['mujian']['inputs']})
        self.assertEqual(update['mujian']['appliedDefinitionHash'], update['mujian']['definitionHash'])

    def test_optimized_clean_migration_and_repeated_sync_need_no_review(self):
        run = run_fixture(); old = legacy_graph(run); browser_save_defaults(old)
        fresh = build_graph(run)
        intended = next(node for node in fresh['nodes'] if node['type'] == 'video')
        apply_direction(intended, '保持既定角色和道具归属，沿原有动作顺序连续呈现。', {'skills': ['expert']}, 'new-guidance')
        proposal = deepcopy(fresh)
        merged = merge_graph(fresh, old, run['id'])
        mark_merged_optimizations(merged, proposal, old)
        video = next(node for node in merged['nodes'] if node['type'] == 'video')
        self.assertEqual(video['mujian']['mode'], 'reference')
        self.assertFalse(video['mujian'].get('needsReview'))
        self.assertEqual(video['mujian']['promptOptimization']['status'], 'applied')
        self.assertEqual(video['settings']['prompt'], next(node for node in proposal['nodes'] if node['type'] == 'video')['settings']['prompt'])
        browser_save_defaults(merged)
        repeated = merge_graph(deepcopy(proposal), merged, run['id'])
        mark_merged_optimizations(repeated, proposal, merged)
        again = next(node for node in repeated['nodes'] if node['type'] == 'video')
        self.assertFalse(again['mujian'].get('needsReview'))
        self.assertNotIn('proposed', again['mujian'])
        self.assertEqual(again['mujian']['promptOptimization']['status'], 'applied')


if __name__ == '__main__':
    unittest.main()
