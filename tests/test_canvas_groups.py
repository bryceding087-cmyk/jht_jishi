"""Group persistence and compatibility contracts; isolated storage, no model calls."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException


class CanvasGroupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix='jht-groups-')
        spec=importlib.util.spec_from_file_location('groups_test_app',Path(__file__).resolve().parents[1]/'app.py')
        cls.host=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'JIANHUITONG_DATA_DIR':cls.directory.name}):spec.loader.exec_module(cls.host)
        assert cls.host.DATA==Path(cls.directory.name)
    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def payload(self,**changes):
        return self.host.Project(**{'name':'分组画布','nodes':[{'id':'a','type':'prompt'},{'id':'b','type':'prompt'}],
            'edges':[{'from':'a','to':'b'}],'groups':[{'id':'group','title':'场景参考','color':'#4285f4','node_ids':['a','b'],'x':50,'y':40,'width':800,'height':400}],**changes})

    def test_create_update_and_duplicate_preserve_groups_and_generation_edges(self):
        project=self.host.create_project(self.payload())
        self.assertEqual(project['groups'][0]['node_ids'],['a','b'])
        updated=self.host.update_project(project['id'],self.payload(revision=project['revision']))
        duplicate=self.host.duplicate_project(project['id'])
        self.assertEqual(duplicate['groups'],updated['groups'])
        self.assertEqual(duplicate['edges'],[{'from':'a','to':'b'}])

    def test_older_client_updates_keep_groups_but_explicit_ungroup_clears_them(self):
        project=self.host.create_project(self.payload())
        old=self.host.Project(name='旧客户端',nodes=project['nodes'],edges=project['edges'])
        updated=self.host.update_project(project['id'],old)
        self.assertEqual(updated['groups'],project['groups'])
        cleared=self.host.update_project(project['id'],self.payload(groups=[]))
        self.assertEqual(cleared['groups'],[])

    def test_deleted_members_are_pruned_and_empty_groups_are_removed(self):
        project=self.host.create_project(self.payload())
        updated=self.host.update_project(project['id'],self.payload(nodes=[{'id':'a','type':'prompt'}],edges=[]))
        self.assertEqual(updated['groups'][0]['node_ids'],['a'])
        updated=self.host.update_project(project['id'],self.payload(nodes=[],edges=[]))
        self.assertEqual(updated['groups'],[])

    def test_group_ids_and_membership_cannot_overlap(self):
        for groups in [
            [{'id':'same','node_ids':['a']},{'id':'same','node_ids':['b']}],
            [{'id':'one','node_ids':['a']},{'id':'two','node_ids':['a','b']}],
        ]:
            with self.subTest(groups=groups),self.assertRaises(HTTPException):self.host.create_project(self.payload(groups=groups))

    def test_invalid_geometry_is_rejected_and_color_is_sanitized(self):
        for value in [float('nan'),float('inf'),-1,True,'100px']:
            with self.subTest(value=value),self.assertRaises(HTTPException):
                self.host.create_project(self.payload(groups=[{'id':'g','node_ids':['a'],'width':value}]))
        project=self.host.create_project(self.payload(groups=[{'id':'g','node_ids':['a'],'color':'url(secret)'}]))
        self.assertEqual(project['groups'][0]['color'],'#999999')


if __name__=='__main__':unittest.main()
