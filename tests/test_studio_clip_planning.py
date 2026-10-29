"""Pure planning/reference tests: no database, network, or model requests."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from fastapi import HTTPException
from studio_graph import plan_video_clips, clip_storyboard, add_prompt_mentions, compile_reference_prompt, build_graph, digest


def shot(key, seconds, **extra):
    return dict(key=key, title=key, seconds=seconds, sceneId='1-1', sceneKey='scene', references=['hero', 'scene'],
                framing='中景', staging='门前', startState='静立', action='推门', endState='进入房间', camera='推进',
                lighting='自然光', sound='脚步', dialogue=key+'：你好。', continuity='保持衣着', sources=[{'line': key}], **extra)


class StudioClipPlanningTests(unittest.TestCase):
    def test_compact_episode_layout_reserves_portrait_height(self):
        from studio_graph import layout_studio_graph
        for ratio,height in [('16:9',230),('9:16',334)]:
            nodes=[{'id':str(i),'type':'video','settings':{'aspect_ratio':ratio},'mujian':{'role':'video','episode':1}} for i in range(9)]
            nodes += [{'id':role,'type':'prompt','mujian':{'role':role,'episode':1}} for role in ('script','edit')]
            nodes.append({'id':'final','type':'timeline','mujian':{'role':'final'}})
            graph={'nodes':nodes,'groups':[]}
            layout_studio_graph(graph,'run',{})
            self.assertEqual(nodes[1]['x']-nodes[0]['x'],368)
            self.assertEqual(nodes[4]['y']-nodes[0]['y'],height+32)
            group=graph['groups'][0]
            self.assertEqual(nodes[8]['y']+height+24,group['y']+group['height'])

    def test_fractional_shots_use_model_ceiling_and_keep_every_line(self):
        shots=[shot('a',7.5),shot('b',6)]
        result=plan_video_clips(shots, {'durations':[5,10,15]})
        self.assertEqual(len(result),1)
        clip=result[0]
        self.assertEqual(clip['seconds'],15); self.assertEqual(clip['plannedSeconds'],13.5)
        self.assertEqual(clip['sourceShots'],shots)
        prompt=clip_storyboard(clip)
        for text in ['0–7.5 秒','7.5–13.5 秒','13.5–15 秒','a：你好。','b：你好。','不增加台词或剧情']:
            self.assertIn(text,prompt)
        self.assertEqual(len(plan_video_clips(shots, {'durations':[5,10]})),2)

    def test_splits_at_scene_cut_capacity_and_unknown_model(self):
        for field,value in [('sceneId','1-2'),('sceneKey','another'),('cutBefore',True)]:
            a,b=shot('a',4),shot('b',4); b[field]=value
            self.assertEqual(len(plan_video_clips([a,b],{'durations':[15]})),2)
        self.assertEqual(len(plan_video_clips([shot('a',4),shot('b',4)])),2)
        b=shot('b',4);b['references']=['new','scene']
        self.assertEqual(len(plan_video_clips([shot('a',4),b],{'durations':[15]},2)),2)
        with self.assertRaises(HTTPException):plan_video_clips([shot('a',16)],{'durations':[15]})
        with self.assertRaises(HTTPException):plan_video_clips([shot('a',4)],{'durations':[15]},1)

    def test_variant_families_group_pending_references_and_only_clips_per_episode(self):
        catalog=[dict(key=k,type=t,name=name,variant=v,facts='原文',design='暖光',anchors=[],sources=[]) for k,t,name,v in
                 [('hero','character','小林','默认'),('coat','character','小林','雨衣'),('scene','scene','车站','默认'),('night','scene','车站','夜晚')]]
        run={'id':'test-run','title':'回家','preferences':{'aspect_ratio':'16:9','models':{},'studio_video_mode':'reference'},
             'studio':{'brief':{'episode_count':1},'catalog':catalog,'documents':{'episode-1':{'status':'current','content':'正文','version':1}},
                       'extractions':{'1':{'source_digest':digest('正文'),'shots':[shot('a',4),shot('b',4)]}}}}
        graph=build_graph(run,capabilities={'durations':[5,10,15],'max_images':9})
        self.assertEqual({g['title'] for g in graph['groups']},{'人物 · 小林','场景 · 车站','第 1 集 · 视频片段'})
        videos=[n for n in graph['nodes'] if n['type']=='video']
        self.assertEqual(len(videos),1);self.assertEqual(videos[0]['settings']['duration'],10)
        rain=next(n for n in graph['nodes'] if n['title']=='小林 · 雨衣')
        self.assertEqual(len(rain['image_settings']['mentions']),1)
        self.assertFalse(rain['image_settings']['mentions'][0]['asset_id'])
        self.assertIn('@小林',rain['image_settings']['prompt'])
        # A rebuild does not move or overwrite the user's edited prompt.
        rain['x']=932;rain['image_settings']['prompt']='手动设计'
        merged=build_graph(run,graph,{'durations':[5,10,15],'max_images':9})
        saved=next(n for n in merged['nodes'] if n['id']==rain['id'])
        self.assertEqual((saved['x'],saved['image_settings']['prompt']),(932,'手动设计'))

    def test_reference_names_cannot_collide_or_rewrite_inserted_tokens(self):
        graph={'nodes':[{'id':'one','title':'图片2'},{'id':'two','title':'图片2'},
                        {'id':'target','type':'video','title':'片段','settings':{'prompt':'图片1进入，图片2离开'},'mujian':{'inputs':[{'nodeId':'one','kind':'image'},{'nodeId':'two','kind':'image'}]}}]}
        # Only generator nodes have settings/meta; reference definitions are still real nodes.
        for n in graph['nodes'][:2]:n['type']='asset'
        add_prompt_mentions(graph)
        settings=graph['nodes'][-1]['settings']
        self.assertEqual(settings['prompt'],'@图片2进入，@图片2（2）离开')
        self.assertEqual(len({m['token'] for m in settings['mentions']}),2)

    def test_prompt_compilation_follows_current_binding_and_never_falls_back_to_old_asset(self):
        assets={'new':{'kind':'image'},'old':{'kind':'image'},'voice':{'kind':'audio'}}
        host=SimpleNamespace(require_live_asset=lambda aid:assets[aid])
        node={'type':'video','settings':{'mentions':[{'token':'@小林','node_id':'hero','asset_id':'old','name':'小林','kind':'image'},
                                                  {'token':'@声音','asset_id':'voice','name':'声音','kind':'audio'}]}}
        graph={'nodes':[{'id':'hero','type':'asset','assetId':'new'}]}
        self.assertEqual(compile_reference_prompt(host,graph,node,'@小林 按 @声音 说话',['new','voice']),'图片1 按 音频1 说话')
        for nodes in ([{'id':'hero','type':'asset'}],[]):
            with self.assertRaises(HTTPException):compile_reference_prompt(host,{'nodes':nodes},node,'@小林',['old'])


if __name__=='__main__':unittest.main()
