"""Deletion is scoped to a generation turn and keeps shared inputs intact."""
import test_sidebar_metadata


class ConversationTurnActionTests(test_sidebar_metadata.SidebarMetadataTests):
    def make_turn(self, media_kind, suffix='one', cid=None):
        if not cid:
            status, _, conv = self.request('POST', '/api/conversations', {
                'title': '原对话', 'kind': media_kind, 'prompt': '原始内容',
                'settings': {'size': '1K'}, 'request_id': media_kind + suffix,
            })
            self.assertEqual(status, 201)
            cid = conv['id']
        tid = media_kind + '-' + suffix
        aid = 'asset-' + tid
        asset = self.host.save('asset', {
            'id': aid, 'name': '生成结果', 'kind': media_kind,
            'task_id': tid, 'project_id': cid, 'url': '/uploads/' + aid + '.wav',
        })
        task = {'id': tid, 'status': 'done', 'project_id': cid, 'prompt': '本段\n完整内容',
                'size': '2K', 'count': 2, 'reference_asset_ids': ['shared-input'],
                'asset': asset, 'assets': [asset], 'created_at': 1}
        record_kind = {'image': 'image_task', 'audio': 'audio_task', 'video': 'task'}[media_kind]
        if media_kind == 'video':
            task.update(video_url=asset['url'], params={'prompt': task['prompt'], 'resolution': '720p', 'asset_ids': ['shared-input']})
        self.host.save(record_kind, task)
        return cid, tid, aid, record_kind

    def test_delete_one_turn_preserves_other_turn_and_inputs(self):
        self.host.save('asset', {'id': 'shared-input', 'kind': 'image', 'name': '参考图'})
        for media_kind in ('image', 'video', 'audio'):
            with self.subTest(kind=media_kind):
                cid, tid, aid, record_kind = self.make_turn(media_kind)
                _, second, second_asset, _ = self.make_turn(media_kind, 'two', cid)
                path = '/api/conversations/' + cid
                turn = self.request('GET', path)[2]['tasks'][0]
                self.assertEqual(turn['prompt'], '本段\n完整内容')
                self.assertEqual(turn['generation_settings'].get('resolution') if media_kind == 'video' else turn['generation_settings']['size'], '720p' if media_kind == 'video' else '2K')
                status, _, result = self.request('DELETE', path + '/tasks/' + tid)
                self.assertEqual(status, 200)
                self.assertFalse(result['conversation_deleted'])
                self.assertTrue(self.host.get('asset', aid)['deleted_at'])
                self.assertTrue(self.host.get(record_kind, tid)['deleted_at'])
                endpoint = {'image': '/api/images/generations/', 'audio': '/api/audio/generations/', 'video': '/api/tasks/'}[media_kind]
                self.assertEqual(self.request('GET', endpoint + tid)[0], 404)
                self.assertNotIn('deleted_at', self.host.get('asset', second_asset))
                self.assertNotIn('deleted_at', self.host.get('asset', 'shared-input'))
                self.assertEqual([t['id'] for t in self.request('GET', path)[2]['tasks']], [second])
                self.assertTrue(self.request('DELETE', path + '/tasks/' + second)[2]['conversation_deleted'])
                self.assertEqual(self.request('GET', path)[0], 404)
                self.assertNotIn(cid, [c['id'] for c in self.request('GET', '/api/conversations')[2]])

    def test_delete_rejects_shared_assets_and_rolls_back(self):
        cid, tid, aid, record_kind = self.make_turn('audio', 'used')
        self.host.save('project', {'id': 'using-audio', 'name': '引用画布', 'nodes': [{'id': 'audio', 'type': 'audio', 'assetId': aid}]})
        status, _, _ = self.request('DELETE', '/api/conversations/' + cid + '/tasks/' + tid)
        self.assertEqual(status, 409)
        self.assertNotIn('deleted_at', self.host.get('asset', aid))
        self.assertNotIn('deleted_at', self.host.get(record_kind, tid))
        self.assertNotIn('deleted_at', self.host.get('conversation', cid))

    def test_deleted_image_and_audio_outputs_remain_identifiable_in_conversations(self):
        for kind in ('image', 'audio'):
            with self.subTest(kind=kind):
                cid, tid, aid, record_kind = self.make_turn(kind, 'deleted-output')
                self.assertEqual(self.request('DELETE', '/api/assets/' + aid)[0], 204)
                status, _, conversation = self.request('GET', '/api/conversations/' + cid)
                self.assertEqual(status, 200)
                task = conversation['tasks'][0]
                self.assertEqual(task['deleted_asset_ids'], [aid])
                self.assertIsNone(task['asset'])
                if kind == 'image':
                    self.assertEqual(task['assets'], [])
                self.assertEqual(task['status'], 'done')
                self.assertEqual(task['prompt'], '本段\n完整内容')
                self.assertEqual(task['generation_settings']['size'], '2K')
                endpoint = '/api/images/generations/' if kind == 'image' else '/api/audio/generations/'
                self.assertEqual(self.request('GET', endpoint + tid)[2]['deleted_asset_ids'], [aid])
                self.assertNotIn('deleted_at', self.host.get(record_kind, tid))

    def test_partial_image_deletion_keeps_live_results_and_does_not_mark_failed_tasks(self):
        cid, tid, first, record_kind = self.make_turn('image', 'partial-output')
        second = self.host.save('asset', {'id': 'remaining-image', 'kind': 'image', 'name': '第二张',
            'task_id': tid, 'project_id': cid, 'url': '/uploads/second.png'})
        task = self.host.get(record_kind, tid)
        task['assets'].append(second)
        self.host.save(record_kind, task)
        self.assertEqual(self.request('DELETE', '/api/assets/' + first)[0], 204)
        public = self.request('GET', '/api/conversations/' + cid)[2]['tasks'][0]
        self.assertEqual(public['deleted_asset_ids'], [first])
        self.assertEqual([asset['id'] for asset in public['assets']], [second['id']])
        self.assertEqual(self.request('DELETE', '/api/assets/' + second['id'])[0], 204)
        public = self.request('GET', '/api/conversations/' + cid)[2]['tasks'][0]
        self.assertEqual(set(public['deleted_asset_ids']), {first, second['id']})
        self.assertEqual(public['assets'], [])
        for serializer in (self.host.image_task_public, self.host.audio_task_public):
            self.assertNotIn('deleted_asset_ids', serializer({'id': 'failed-no-output', 'status': 'failed', 'error': '生成失败'}))

    def test_delete_checks_ownership_task_scope_and_pending_state(self):
        cid, tid, aid, record_kind = self.make_turn('image', 'scoped')
        other, _, _, _ = self.make_turn('image', 'other')
        self.assertEqual(self.request('DELETE', '/api/conversations/' + other + '/tasks/' + tid)[0], 404)
        task = self.host.get(record_kind, tid)
        self.host.save(record_kind, {**task, 'status': 'running'})
        self.assertEqual(self.request('DELETE', '/api/conversations/' + cid + '/tasks/' + tid)[0], 409)
        self.host.save(record_kind, task)
        self.login_as_member()
        self.assertEqual(self.request('DELETE', '/api/conversations/' + cid + '/tasks/' + tid)[0], 404)
        self.assertNotIn('deleted_at', self.host.get('asset', aid))
