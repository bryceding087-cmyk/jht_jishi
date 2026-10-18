"""Project metadata and folder persistence, including older canvas clients."""
from test_canvas_groups import CanvasGroupsTests
from fastapi import HTTPException


class ProjectFolderTests(CanvasGroupsTests):
    def test_folder_assignment_survives_old_client_save_and_duplicate(self):
        h=self.host;folder=h.create_project_folder(h.AssetFolder(name='宣传片'))
        project=h.create_project(h.Project(name='项目',folder_id=folder['id'],nodes=[{'id':'text','type':'prompt'}]))
        saved=h.update_project(project['id'],h.Project(name='项目',nodes=project['nodes'],revision=1))
        self.assertEqual(saved['folder_id'],folder['id'])
        self.assertEqual(h.duplicate_project(project['id'])['folder_id'],folder['id'])

    def test_metadata_preserves_graph_and_rejects_stale_revision(self):
        h=self.host;project=h.create_project(self.payload())
        updated=h.update_project_metadata(project['id'],h.ProjectMetadata(name='新名字',revision=1))
        self.assertEqual(updated['nodes'],project['nodes']);self.assertEqual(updated['edges'],project['edges'])
        self.assertEqual(updated['groups'],project['groups']);self.assertEqual(updated['revision'],2)
        with self.assertRaises(HTTPException) as error:h.update_project_metadata(project['id'],h.ProjectMetadata(name='旧页面',revision=1))
        self.assertEqual(error.exception.status_code,409)

    def test_delete_folder_preserves_projects_including_recycled(self):
        h=self.host;folder=h.create_project_folder(h.AssetFolder(name='待移除文件夹'))
        project=h.create_project(h.Project(name='保留项目',folder_id=folder['id']))
        trashed=h.create_project(h.Project(name='回收站内项目',folder_id=folder['id']))
        h.save('project',{**trashed,'deleted_at':1})
        h.delete_project_folder(folder['id'])
        self.assertEqual(h.get('project',project['id'])['folder_id'],'')
        self.assertEqual(h.get('project',trashed['id'])['folder_id'],'')
        self.assertEqual(h.get('project',trashed['id'])['deleted_at'],1)

    def test_invalid_folder_name_and_missing_destination_are_rejected(self):
        h=self.host;h.create_project_folder(h.AssetFolder(name='唯一文件夹'))
        for name in ['唯一文件夹','  ']:
            with self.assertRaises(HTTPException):h.create_project_folder(h.AssetFolder(name=name))
        with self.assertRaises(HTTPException):h.create_project(h.Project(name='无效目标',folder_id='missing-folder'))
        project=h.create_project(h.Project(name='原项目'))
        with self.assertRaises(HTTPException):h.update_project_metadata(project['id'],h.ProjectMetadata(folder_id='missing-folder'))
