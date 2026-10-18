"""Three-media library cleanup preserves editable documents and media references."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager, closing
from media_library import migrate_media_library


class MediaLibraryTests(unittest.TestCase):
    def test_cleanup_is_repeatable_and_keeps_document_content_and_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'uploads').mkdir()
            (root/'uploads/old.txt').write_text('完整的文本内容',encoding='utf-8')
            @contextmanager
            def connect():
                db=sqlite3.connect(root/'store.sqlite3')
                try:
                    with db:yield db
                finally:db.close()
            with connect() as db:
                db.execute('CREATE TABLE records(kind TEXT,id TEXT PRIMARY KEY,body TEXT)')
                records=[
                    ('asset',{'id':'text','kind':'text','content':'完整的文本内容','url':'/uploads/old.txt'}),
                    ('asset',{'id':'script','kind':'script','script_data':{'scenes':[{'description':'已编写镜头'}]}}),
                    ('asset',{'id':'image','kind':'prop','url':'/uploads/picture.png'}),
                    ('asset',{'id':'video','kind':'video','url':'/uploads/video.mp4'}),
                    ('character',{'id':'role','asset_id':'image','name':'人物'}),
                    ('character',{'id':'empty-role','name':'无图角色'}),
                    ('agent_artifact',{'id':'document','asset_id':'text','content':'完整的创作记录'}),
                    ('project',{'id':'project','revision':3,'nodes':[
                        {'id':'prompt','type':'prompt','sourceAssetId':'text','settings':{'prompt':'用户编辑内容'}},
                        {'id':'copy','type':'asset','assetId':'text'},
                        {'id':'script-copy','type':'script','sourceAssetId':'script'},
                        {'id':'role-node','type':'character','characterId':'role'},
                        {'id':'empty-node','type':'character','characterId':'empty-role'},
                        {'id':'target','type':'video','settings':{'prompt':'引用 @原文','mentions':[{'asset_id':'text','token':'@原文'}],'asset_ids':['image','text']}}
                    ],'edges':[{'from':'role-node','to':'target'},{'from':'empty-node','to':'target'}],
                    'groups':[{'id':'group','node_ids':['role-node','empty-node']}],'selected_asset_ids':['text','video']})
                ]
                for kind,body in records:db.execute('INSERT INTO records VALUES(?,?,?)',(kind,body['id'],json.dumps(body)))
            report=migrate_media_library(connect,root)
            self.assertEqual(report,{'removed_assets':2,'removed_characters':2,'converted_images':1,'updated_projects':1})
            self.assertFalse((root/'uploads/old.txt').exists())
            backups=list((root/'backups').glob('*.sqlite3'));self.assertEqual(len(backups),1)
            with closing(sqlite3.connect(backups[0])) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM records WHERE kind="character"').fetchone()[0],2)
            with connect() as db:
                saved={rid:json.loads(body) for rid,body in db.execute('SELECT id,body FROM records')}
            self.assertNotIn('text',saved);self.assertNotIn('script',saved);self.assertNotIn('role',saved)
            self.assertEqual(saved['image']['kind'],'image')
            project=saved['project'];nodes={node['id']:node for node in project['nodes']}
            self.assertEqual(project['revision'],4)
            self.assertEqual(nodes['prompt']['settings']['prompt'],'用户编辑内容')
            self.assertEqual(nodes['copy']['settings']['prompt'],'完整的文本内容')
            self.assertEqual(nodes['script-copy']['script_data']['scenes'][0]['description'],'已编写镜头')
            self.assertEqual(nodes['role-node']['type'],'asset');self.assertEqual(nodes['role-node']['assetId'],'image')
            self.assertEqual(project['edges'],[{'from':'role-node','to':'target'}])
            self.assertEqual(project['groups'][0]['node_ids'],['role-node'])
            self.assertEqual(nodes['target']['settings']['prompt'],'引用 完整的文本内容')
            self.assertEqual(nodes['target']['settings']['asset_ids'],['image'])
            self.assertEqual(saved['document']['content'],'完整的创作记录')
            self.assertEqual(saved['document']['asset_id'],'')
            self.assertEqual(migrate_media_library(connect,root)['removed_assets'],0)
            self.assertEqual(len(list((root/'backups').glob('*.sqlite3'))),1)


if __name__=='__main__':unittest.main()
