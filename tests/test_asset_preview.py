"""Preview metadata uses task records and never exposes provider secrets."""
import unittest
from fastapi import HTTPException
from test_canvas_groups import CanvasGroupsTests as Storage

class AssetPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):Storage.setUpClass();cls.host=Storage.host
    @classmethod
    def tearDownClass(cls):Storage.tearDownClass()
    def setUp(self):
        with self.host.connect() as db:db.execute('DELETE FROM records')
    def test_generated_image_metadata_and_references(self):
        h=self.host
        h.save('image_model_config',{'id':'model-a','name':'测试图片模型','api_key':'never-expose'})
        h.save('asset',{'id':'ref','kind':'image','name':'参考图','url':'/uploads/ref.png'})
        h.save('image_task',{'id':'task-a','prompt':'完整提示词','model_config_id':'model-a','model':'seedream','size':'2K','aspect_ratio':'3:4','count':2,'reference_asset_ids':['ref','missing']})
        h.save('asset',{'id':'image-a','kind':'image','name':'生成图','source':'generated','task_id':'task-a','url':'/uploads/result.png'})
        result=h.asset_details('image-a')
        self.assertEqual(result['generation']['prompt'],'完整提示词')
        self.assertEqual(result['generation']['model_name'],'测试图片模型')
        self.assertEqual(result['generation']['size'],'2K')
        self.assertEqual([r['id'] for r in result['generation']['references']],['ref'])
        self.assertNotIn('never-expose',str(result))
    def test_video_metadata_and_upload_without_generation(self):
        h=self.host
        h.save('task',{'id':'video-task','params':{'prompt':'视频提示词','model_name':'视频模型','duration':5,'resolution':'720p','asset_ids':[]}})
        h.save('asset',{'id':'video-a','kind':'video','name':'视频','task_id':'video-task','duration':5})
        self.assertEqual(h.asset_details('video-a')['generation']['duration'],5)
        h.save('asset',{'id':'upload-a','kind':'image','name':'上传图片'})
        self.assertIsNone(h.asset_details('upload-a')['generation'])
        self.assertEqual(h.asset_details('upload-a')['creator_name'],'')
        h.save('asset',{'id':'deleted','kind':'image','deleted_at':1})
        with self.assertRaises(HTTPException):h.asset_details('deleted')

if __name__=='__main__':unittest.main()
