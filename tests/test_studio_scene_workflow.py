"""Scene concurrency and recovery against a disposable application database."""
from collections import Counter
from copy import deepcopy
import json
from threading import Barrier, Lock
import unittest
from unittest.mock import patch

from studio_graph import digest, scenes_for, validate_extraction
import test_agent_studio as studio_fixture
from studio_fixtures import reply


def context_of(messages):
    return json.loads(messages[-1]['content'])


def decode(response):
    return json.loads(response['choices'][0]['message']['content'])


def encode(value):
    return {'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}]}


class StudioSceneWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        studio_fixture.AgentStudioTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    setUp = studio_fixture.AgentStudioTests.setUp
    create = studio_fixture.AgentStudioTests.create
    step = studio_fixture.AgentStudioTests.step
    message = studio_fixture.AgentStudioTests.message
    model_reply = staticmethod(reply)
    add_media = studio_fixture.AgentStudioTests.add_media
    image = studio_fixture.AgentStudioTests.image
    audio = studio_fixture.AgentStudioTests.audio
    video = studio_fixture.AgentStudioTests.video

    def canvas_job(self):
        rid = self.create('写剧本：创作一个 2 集故事')
        self.step(rid)
        self.message(rid, action='studio_canvas')
        self.service.studio_route(rid)
        self.service.studio_patch(rid, style_bible='暖色日光，保持角色身份和空间连续。')
        return rid

    def episode_response(self, run, episode, catalog=()):
        doc = run['studio']['documents'][f'episode-{episode}']
        context = {'episode': episode, 'scenes': scenes_for(doc['content'], episode), 'catalog': list(catalog)}
        return decode(reply({}, [{'role': 'system', 'content': '逐场阅读完整剧本'},
                                  {'role': 'user', 'content': json.dumps(context)}]))

    def legacy_receipt(self, rid, episode, status, result=None, suffix=''):
        run = self.service.get(rid)
        version = run['studio']['documents'][f'episode-{episode}']['version']
        key = f'studio:{digest(run["studio_job"]["id"])[:24]}:extract:{episode}:{version}{suffix}'
        oid = f'{rid}:op:{key}:0'
        record = {'id': oid, 'run_id': rid, 'key': key, 'status': status, 'attempt': 0}
        if result is not None:
            record['result'] = deepcopy(result)
        if status == 'invalid':
            record['error'] = '镜头引用了尚未定义的角色、场景或道具'
        self.host.save('agent_operation', record)
        return oid

    def test_appendix_boundary_fix_reuses_old_scene_receipts_without_resubmission(self):
        from fastapi import HTTPException
        rid = self.canvas_job()
        docs = deepcopy(self.service.get(rid)['studio']['documents'])
        docs['episode-1']['content'] += '\n## 三、资产登记\n统一美术：低饱和写实摄影，9:16竖构图。'
        self.service.studio_patch(rid, documents=docs)
        last = scenes_for(docs['episode-1']['content'], 1)[-1]['id']
        def old_validate(value, content, episode, catalog, **kwargs):
            result = validate_extraction(value, content, episode, catalog, **kwargs)
            if kwargs.get('scene_ids') == [last]:
                raise HTTPException(422, '场 ' + last + ' 的原文对白遗漏或被改写：低饱和写实摄影')
            return result
        def old_scenes(content, episode, **kwargs):
            return scenes_for(content, episode, include_appendices=True)
        with patch('studio_extraction.scenes_for', side_effect=old_scenes), patch('studio_extraction.validate_extraction', side_effect=old_validate):
            self.service.work(rid)
        blocked = self.service.get(rid)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('对白遗漏', blocked['error'])
        old_receipts = [op for op in self.host.listing('agent_operation') if op['run_id'] == rid and ':scene:' in op['key']]
        saved_scenes = {shot['sceneId'] for op in old_receipts for shot in op.get('result', {}).get('shots', [])}
        self.assertIn(last, saved_scenes)
        def resume_reply(config, messages):
            context = context_of(messages)
            if context.get('task') == 'extract_scene':
                self.assertNotIn(context['scenes'][0]['id'], saved_scenes, 'Known scene result was submitted again')
            return reply(config, messages)
        self.message(rid, action='resume')
        with patch.object(self.host, 'llm_json', side_effect=resume_reply):
            finished = self.step(rid)
        self.assertEqual(finished['status'], 'completed')
        self.assertTrue(finished['studio']['canvas_project_id'])
        self.assertEqual(finished['studio']['documents'], docs)
        self.assertTrue(any(self.host.raw_record('agent_operation', op['id'])['status'] == 'succeeded'
                            for op in old_receipts if op['status'] == 'invalid'))

    def test_scene_requests_reach_but_never_exceed_three_in_parallel(self):
        rid = self.canvas_job()
        barrier, lock = Barrier(3), Lock()
        state = {'active': 0, 'peak': 0, 'entered': 0}
        calls = Counter()

        def concurrent(config, messages):
            context = context_of(messages)
            if context.get('task') != 'extract_scene':
                return reply(config, messages)
            sid = context['scenes'][0]['id']
            with lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
                state['entered'] += 1
                entry = state['entered']
                calls[sid] += 1
            try:
                # A serial implementation cannot reach this barrier. Later
                # tasks do not wait for a nonexistent second group of three.
                if entry <= 3:
                    barrier.wait(timeout=10)
                return reply(config, messages)
            finally:
                with lock:
                    state['active'] -= 1

        with patch.object(self.host, 'llm_json', side_effect=concurrent):
            run = self.step(rid)
        self.assertEqual(state['peak'], 3)
        self.assertEqual(state['active'], 0)
        self.assertEqual(calls, Counter({'1-1': 1, '1-2': 1, '2-1': 1, '2-2': 1}))
        for ep in (1, 2):
            shots = run['studio']['extractions'][str(ep)]['shots']
            self.assertEqual([shot['sceneId'] for shot in shots], [f'{ep}-1', f'{ep}-2'])
            self.assertEqual([shot['key'] for shot in shots], [f'ep{ep}-shot1', f'ep{ep}-shot2'])
        progress = run['studio']['extraction_progress']
        self.assertEqual((progress['current'], progress['total'], progress['running'], progress['queued']), (4, 4, 0, 0))
        self.assertEqual(progress['concurrency'], 3)
        self.assertEqual(progress['status'], 'completed')
        self.assertFalse(self.provider_calls)

    def test_failed_scene_keeps_sibling_cache_and_explicit_retry_does_not_repeat_them(self):
        rid = self.canvas_job()
        barrier, lock = Barrier(3), Lock()
        calls, succeeded = Counter(), set()
        first_entries = 0

        def one_bad_scene(config, messages):
            nonlocal first_entries
            context = context_of(messages)
            response = reply(config, messages)
            if context.get('task') != 'extract_scene':
                return response
            sid = context['scenes'][0]['id']
            with lock:
                calls[sid] += 1
                first_entries += 1
                entry = first_entries
            if entry <= 3:
                barrier.wait(timeout=10)
            if sid == '1-1':
                value = decode(response)
                value['shots'][0]['characterKeys'] = ['missing_character']
                return encode(value)
            with lock:
                succeeded.add(sid)
            return response

        with patch.object(self.host, 'llm_json', side_effect=one_bad_scene):
            self.service.work(rid)
        blocked = self.service.get(rid)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertEqual(calls['1-1'], 2)
        self.assertGreaterEqual(len(succeeded), 2)
        cache = blocked['studio']['scene_extractions']
        cached_scenes = {shot['sceneId'] for item in cache.values() for shot in item['shots']}
        self.assertEqual(cached_scenes, succeeded)
        documents = deepcopy(blocked['studio']['documents'])
        completed = deepcopy(blocked['studio']['extractions'])
        retry_calls = Counter()

        def corrected(config, messages):
            context = context_of(messages)
            if context.get('task') == 'extract_scene':
                retry_calls[context['scenes'][0]['id']] += 1
            return reply(config, messages)

        self.message(rid, action='retry')
        with patch.object(self.host, 'llm_json', side_effect=corrected):
            finished = self.step(rid)
        self.assertEqual(retry_calls['1-1'], 1)
        self.assertTrue(all(retry_calls[sid] == 0 for sid in succeeded))
        self.assertEqual(finished['studio']['documents'], documents)
        for ep, extraction in completed.items():
            self.assertEqual(finished['studio']['extractions'][ep], extraction)
        self.assertEqual(set(finished['studio']['extractions']), {'1', '2'})
        self.assertTrue(finished['studio']['canvas_project_id'])

    def test_old_failed_response_recovers_new_variant_locally_and_keeps_prior_episode(self):
        rid = self.canvas_job()
        run = self.service.get(rid)
        doc1 = run['studio']['documents']['episode-1']
        first_raw = self.episode_response(run, 1)
        first_raw['assets'][0]['variant'] = '晴天灰衣'
        first = validate_extraction(first_raw, doc1['content'], 1, [])
        extraction1 = {'source_digest': digest(doc1['content']), 'shots': first['shots'], 'warnings': first['warnings']}
        self.service.studio_patch(rid, catalog=first['assets'], extractions={'1': extraction1})
        original_assets = deepcopy(first['assets'])
        second_raw = self.episode_response(run, 2, first['assets'])
        scene = scenes_for(run['studio']['documents']['episode-2']['content'], 2)[0]
        second_raw['assets'] = [{'key': 'c11', 'reuseId': 'c01', 'name': '小林', 'type': 'character',
            'variant': '雨天蓝雨衣', 'facts': '穿蓝雨衣', 'design': '沿用人物身份，蓝色雨衣', 'anchors': ['同一张脸'],
            'sourceScene': scene['id'], 'sourceLine': scene['line_start'] + 3}]
        for shot in second_raw['shots']:
            shot['characterKeys'] = ['c11']
        oid = self.legacy_receipt(rid, 2, 'invalid', second_raw)
        self.service.patch(rid, status='blocked', error='镜头引用了尚未定义的角色、场景或道具')
        self.message(rid, action='resume')

        def only_canvas_optimization(config, messages):
            self.assertEqual(context_of(messages).get('task'), 'optimize_node_prompts')
            return reply(config, messages)

        with patch.object(self.host, 'llm_json', side_effect=only_canvas_optimization):
            finished = self.step(rid)
        self.assertEqual(finished['studio']['documents'], run['studio']['documents'])
        self.assertEqual(finished['studio']['extractions']['1'], extraction1)
        self.assertEqual(finished['studio']['extractions']['2']['recovered_from'], oid)
        assets = {item['key']: item for item in finished['studio']['catalog']}
        for original in original_assets:
            self.assertEqual(assets[original['key']], original)
        self.assertEqual(assets['c11']['variant'], '雨天蓝雨衣')
        self.assertEqual(assets['c11']['baseKey'], 'c01')
        self.assertTrue(all(shot['characterKeys'] == ['c11'] for shot in finished['studio']['extractions']['2']['shots']))
        self.assertEqual(self.host.raw_record('agent_operation', oid)['result'], second_raw)
        self.assertFalse(self.provider_calls)

    def test_registering_missing_asset_on_retry_preserves_unrelated_scene_cache(self):
        rid = self.canvas_job()
        barrier, lock = Barrier(3), Lock()
        entered = 0
        first_calls = Counter()

        def missing_photo(config, messages):
            nonlocal entered
            context = context_of(messages)
            response = reply(config, messages)
            if context.get('task') != 'extract_scene':
                return response
            sid = context['scenes'][0]['id']
            with lock:
                entered += 1
                entry = entered
                first_calls[sid] += 1
            if entry <= 3:
                barrier.wait(timeout=10)
            if sid == '1-2':
                value = decode(response)
                value['shots'][0]['propKeys'].append('p02')
                return encode(value)
            return response

        with patch.object(self.host, 'llm_json', side_effect=missing_photo):
            self.service.work(rid)
        blocked = self.service.get(rid)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertEqual(first_calls['1-2'], 2)
        self.assertIn('p02', json.dumps(blocked['studio']['asset_registration_issues']))
        cache = deepcopy(blocked['studio']['scene_extractions'])
        completed_scenes = {shot['sceneId'] for pack in cache.values() for shot in pack['shots']}
        self.assertGreaterEqual(len(completed_scenes), 2)
        retry_calls, registrations = Counter(), []

        def add_missing_photo(config, messages):
            context = context_of(messages)
            response = reply(config, messages)
            if context.get('task') == 'register_assets':
                registrations.append(context)
                if context['episode'] == 1:
                    scene = next(s for s in context['scenes'] if s['id'] == '1-2')
                    value = decode(response)
                    value['assets'].append({'key': 'p02', 'type': 'prop', 'name': '旧照片', 'variant': '',
                        'facts': '小林查看的旧照片', 'design': '泛黄纸张', 'anchors': ['同一张照片'],
                        'sourceScene': scene['id'], 'sourceLine': scene['line_start'] + 3})
                    return encode(value)
            if context.get('task') == 'extract_scene':
                sid = context['scenes'][0]['id']
                retry_calls[sid] += 1
                if sid == '1-2':
                    self.assertIn('p02', {asset['key'] for asset in context['catalog']})
                    value = decode(response)
                    value['shots'][0]['propKeys'].append('p02')
                    return encode(value)
            return response

        self.message(rid, action='retry')
        with patch.object(self.host, 'llm_json', side_effect=add_missing_photo):
            finished = self.step(rid)
        self.assertEqual([context['episode'] for context in registrations], [1])
        self.assertIn('p02', json.dumps(registrations[0]['missing_reference_report']))
        self.assertEqual(retry_calls['1-2'], 1)
        self.assertTrue(all(retry_calls[sid] == 0 for sid in completed_scenes))
        self.assertFalse(finished['studio']['asset_registration_issues'])
        for key, pack in cache.items():
            self.assertEqual(finished['studio']['scene_extractions'][key], pack)
        second_scene = finished['studio']['extractions']['1']['shots'][1]
        self.assertIn('p02', second_scene['references'])
        self.assertTrue(finished['studio']['canvas_project_id'])

    def test_unknown_legacy_receipt_cannot_be_bypassed_by_new_scene_workflow(self):
        for suffix in ('', ':repair'):
            with self.subTest(suffix=suffix):
                rid = self.canvas_job()
                run = self.service.get(rid)
                oid = self.legacy_receipt(rid, 1, 'unknown', suffix=suffix)
                self.service.patch(rid, status='blocked', error='文本请求结果未知')
                before = {op['id'] for op in self.host.listing('agent_operation') if op['run_id'] == rid}
                with patch.object(self.host, 'llm_json', side_effect=AssertionError('Unknown request must not be resubmitted')) as requests:
                    for _ in range(2):
                        self.message(rid, action='resume')
                        self.service.work(rid)
                        blocked = self.service.get(rid)
                        self.assertEqual(blocked['status'], 'blocked')
                        self.assertIn('结果未知', blocked['error'])
                    requests.assert_not_called()
                self.assertEqual(self.service.get(rid)['studio']['documents'], run['studio']['documents'])
                self.assertEqual(self.host.raw_record('agent_operation', oid)['status'], 'unknown')
                self.assertEqual({op['id'] for op in self.host.listing('agent_operation') if op['run_id'] == rid}, before)

    def test_model_line_ranges_and_dialogue_objects_complete_without_repair(self):
        rid=self.canvas_job()
        repairs=[]
        def structured_reply(config,messages):
            context=context_of(messages)
            response=reply(config,messages)
            if context.get('task') in ('register_assets','extract_scene'):
                if context.get('repair'):repairs.append(context['task'])
                value=decode(response)
                for item in value.get('assets',[])+value.get('shots',[]):
                    line=item['sourceLine'];item['sourceLine']=f'{line}-{line}'
                for item in value.get('shots',[]):
                    item['dialogue']=[{'speaker':'小林','text':item['dialogue'],'offscreen':False}]
                return encode(value)
            return response
        with patch.object(self.host,'llm_json',side_effect=structured_reply):
            run=self.step(rid)
        self.assertEqual(run['status'],'completed',run.get('error'))
        self.assertTrue(run['studio']['canvas_project_id'])
        self.assertEqual(repairs,[])
        for pack in run['studio']['extractions'].values():
            self.assertTrue(all(type(s['sourceLine']) is int and isinstance(s['dialogue'],str) for s in pack['shots']))

    def test_previous_scene_contract_recovers_received_ranges_without_another_text_request(self):
        import studio_extraction as extraction
        from fastapi import HTTPException
        rid=self.canvas_job()
        real_model=self.host.internal_registry_model
        def old_model(kind,mid=None):
            model=deepcopy(real_model(kind,mid))
            if kind=='video':model['capabilities'].pop('multi_shot',None)
            return model
        def old_validation(value,*args,**kwargs):
            if any(isinstance(s.get('sourceLine'),str) for s in value.get('shots',[])):
                raise HTTPException(422,'资产或镜头的原文场次、行号无效')
            return validate_extraction(value,*args,**kwargs)
        def ranges(config,messages):
            value=decode(reply(config,messages))
            if context_of(messages).get('task')=='extract_scene':
                for item in value['shots']:
                    n=item['sourceLine'];item['sourceLine']=f'{n}-{n}'
                    item['dialogue']=[{'text':item['dialogue'],'speaker':'小林'}]
            return encode(value)
        with patch.object(extraction,'SCENE_PROMPT',extraction.LEGACY_SCENE_PROMPT), \
                patch.object(extraction,'CATALOG_PROMPT',extraction.LEGACY_CATALOG_PROMPT), \
                patch.object(extraction,'validate_extraction',side_effect=old_validation), \
                patch.object(self.host,'internal_registry_model',side_effect=old_model), \
                patch.object(self.host,'llm_json',side_effect=ranges):
            self.service.work(rid)
        self.assertEqual(self.service.get(rid)['status'],'blocked')
        received={s['sceneId'] for op in self.host.listing('agent_operation') if op.get('run_id')==rid
                  for s in (op.get('result') or {}).get('shots',[])}
        self.assertTrue(received)
        self.message(rid,action='resume')
        def optimization_only(config,messages):
            context=context_of(messages)
            if context.get('task')=='extract_scene':self.assertNotIn(context['scenes'][0]['id'],received)
            else:self.assertEqual(context.get('task'),'optimize_node_prompts')
            return reply(config,messages)
        with patch.object(self.host,'llm_json',side_effect=optimization_only):
            run=self.step(rid)
        self.assertEqual(run['status'],'completed',run.get('error'))
        self.assertTrue(run['studio']['canvas_project_id'])

    def test_frozen_asset_usage_summaries_complete_without_format_repair(self):
        for form in ('keys', 'summaries'):
            with self.subTest(form=form):
                rid = self.canvas_job(); repairs = []
                def usage_reply(config, messages):
                    context = context_of(messages); value = decode(reply(config, messages))
                    if context.get('task') == 'extract_scene':
                        if context.get('repair'): repairs.append(context['scenes'][0]['id'])
                        value['assets'] = ([a['key'] for a in context['catalog']] if form == 'keys' else
                            [{k: a[k] for k in ('key', 'name', 'type', 'variant')} | {'usedInShots': [1]}
                             for a in context['catalog']])
                    return encode(value)
                with patch.object(self.host, 'llm_json', side_effect=usage_reply):
                    run = self.step(rid)
                self.assertEqual(run['status'], 'completed', run.get('error'))
                self.assertTrue(run['studio']['canvas_project_id'])
                self.assertEqual(repairs, [])

    def test_blocked_usage_summary_recovers_latest_correction_and_completed_scenes(self):
        import studio_extraction as extraction
        from fastapi import HTTPException
        rid = self.canvas_job()
        def old_validation(value, *args, **kwargs):
            if value.get('assets') and kwargs.get('frozen_catalog'):
                raise HTTPException(422, '资产或镜头的原文场次、行号无效：场 未指定，行 None')
            return validate_extraction(value, *args, **kwargs)
        def usage_reply(config, messages):
            context = context_of(messages); value = decode(reply(config, messages))
            if context.get('task') == 'extract_scene' and context['scenes'][0]['id'] == '1-1':
                value['assets'] = [a['key'] for a in context['catalog']]
                if context.get('repair'):
                    value['assets'] = [{k: a[k] for k in ('key', 'type', 'name', 'variant')} | {'usedInShots': [1]}
                                       for a in context['catalog']]
                    value['shots'][0]['title'] = '修正后的确认地址'
            return encode(value)
        with patch.object(extraction, 'validate_extraction', side_effect=old_validation), \
                patch.object(self.host, 'llm_json', side_effect=usage_reply):
            self.service.work(rid)
        before = self.service.get(rid)
        self.assertEqual(before['status'], 'blocked')
        self.assertGreater(len(before['studio'].get('scene_extractions', {})), 0)
        ops = [op for op in self.host.listing('agent_operation') if op.get('run_id') == rid]
        received = {s['sceneId'] for op in ops for s in (op.get('result') or {}).get('shots', [])}
        correction = next(op for op in ops if op['status'] == 'invalid' and ':repair:' in op['id'])
        self.message(rid, action='resume')
        def no_repeat(config, messages):
            context = context_of(messages)
            if context.get('task') == 'extract_scene':
                self.assertNotIn(context['scenes'][0]['id'], received)
            else:
                self.assertEqual(context.get('task'), 'optimize_node_prompts')
            return reply(config, messages)
        with patch.object(self.host, 'llm_json', side_effect=no_repeat):
            run = self.step(rid)
        self.assertEqual(run['status'], 'completed', run.get('error'))
        self.assertEqual(run['studio']['extractions']['1']['shots'][0]['title'], '修正后的确认地址')
        self.assertEqual(run['studio']['documents'], before['studio']['documents'])
        self.assertEqual(run['studio']['catalog'], before['studio']['catalog'])
        self.assertEqual(self.host.raw_record('agent_operation', correction['id'])['status'], 'succeeded')

    def test_valid_received_answer_cannot_hide_an_unknown_correction(self):
        from fastapi import HTTPException
        rid = self.canvas_job(); run = self.service.get(rid)
        key = f'studio:{digest(run["studio_job"]["id"])[:24]}:check-usage'
        original = {'id': f'{rid}:op:{key}:0', 'run_id': rid, 'key': key, 'status': 'invalid',
                    'attempt': 0, 'result': {'valid': True}, 'error': '旧格式检查失败'}
        correction = {'id': f'{rid}:op:{key}:repair:0', 'run_id': rid, 'key': key + ':repair',
                      'status': 'unknown', 'attempt': 0}
        for op in (original, correction): self.host.save('agent_operation', op)
        with patch.object(self.host, 'llm_json', side_effect=AssertionError('Unknown request must not be replayed')) as requests:
            with self.assertRaises(HTTPException) as error:
                self.service.studio_llm(rid, 'check-usage', '核对素材', '', {}, lambda value: value)
            requests.assert_not_called()
        self.assertIn('结果未知', error.exception.detail)
        self.assertEqual(self.host.raw_record('agent_operation', original['id'])['status'], 'invalid')
        self.assertEqual(self.host.raw_record('agent_operation', correction['id'])['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
