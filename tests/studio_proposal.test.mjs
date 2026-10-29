import assert from 'node:assert/strict';
import test from 'node:test';
import { applyStudioProposal, inputSpecs, validateProductionGraph } from '../public/studio-contract.js';

const source=(id,role='reference')=>({id,type:'asset',title:id,assetId:`media-${id}`,assetUrl:`/uploads/${id}.png`,mujian:{runId:'run',role,inputs:[]}});
const edge=(from,to,extra={})=>({id:`${from}-${to}`,from,to,studio_run_id:'run',...extra});
function fixture(){
  const character=source('character'),scene=source('scene'),first=source('first','firstFrame'),manual=source('manual');
  const video={id:'video',type:'video',title:'用户自己的镜头标题',x:55,y:88,assetId:'old-video',assetUrl:'/uploads/old.mp4',referenceAssetId:'old-video',taskId:'old-task',settings:{prompt:'用户编辑的首帧提示词',video_mode:'first',frame_asset_ids:['media-first'],mentions:[{token:'@first',node_id:'first',kind:'image'}]},
    mujian:{runId:'run',role:'shot',mode:'first',inputs:[{nodeId:'first',kind:'image',slot:'first'}],needsReview:true,mediaStale:true,
      promptOptimization:{status:'proposed',effectivePromptDigest:'old-digest',previousApplied:{version:'old'}},
      proposed:{settings:{prompt:'@character 在 @scene 中转头',video_mode:'reference',mentions:[{token:'@character',node_id:'character',kind:'image'},{token:'@scene',node_id:'scene',kind:'image'}]},image_settings:null,
        mujian:{mode:'reference',inputs:[{nodeId:'character',kind:'image'},{nodeId:'scene',kind:'image'}],shots:[{key:'new-shot'}],sources:[{scene:'1-1',line:8}],definitionHash:'new-definition',appliedDefinitionHash:'new-definition'},
        incomingEdges:[edge('character','video'),edge('scene','video')]}}};
  const edit={id:'edit',type:'timeline',assetId:'old-edit',mujian:{runId:'run',role:'edit',inputs:[{nodeId:'video',kind:'video'}]}};
  return {nodes:[character,scene,first,manual,video,edit],edges:[edge('first','video'),edge('video','edit'),{id:'user-edge',from:'manual',to:'video'}],groups:[]};
}

test('accepting direct references updates metadata, mentions and edges atomically',()=>{
  const graph=fixture(),video=graph.nodes.find(node=>node.id==='video'),edges=graph.edges;
  validateProductionGraph(graph);
  assert.equal(applyStudioProposal(graph,'video',{now:123}),video);
  assert.equal(graph.edges,edges,'Existing canvas edge-array bindings survive');
  assert.equal(video.title,'用户自己的镜头标题');assert.equal(video.x,55);
  assert.equal(video.mujian.mode,'reference');assert.equal(video.settings.video_mode,'reference');
  assert.deepEqual(video.mujian.inputs.map(ref=>ref.nodeId),['character','scene']);
  assert.deepEqual(video.settings.mentions.map(mention=>mention.node_id),['character','scene']);
  assert.deepEqual(inputSpecs(video,graph).map(ref=>ref.nodeId),['character','scene','manual']);
  assert.equal(graph.edges.some(item=>item.from==='first'&&item.to==='video'),false);
  assert.equal(graph.edges.some(item=>item.id==='user-edge'),true);
  assert.equal(video.settings.frame_asset_ids,undefined);
  assert.equal(video.assetId,undefined);assert.equal(video.taskId,undefined);assert.equal(video.referenceAssetId,undefined);
  assert.equal(video.studioOutputAfter,123);
  assert.deepEqual(video.mujian.previousMedia,[{assetId:'old-video',assetUrl:'/uploads/old.mp4'}]);
  assert.equal(video.mujian.needsReview,false);assert.equal(video.mujian.mediaStale,false);
  assert.equal(video.mujian.proposed,undefined);
  assert.equal(video.mujian.appliedDefinitionHash,'new-definition');
  assert.equal(video.mujian.promptOptimization.status,'applied');
  assert.equal(video.mujian.promptOptimization.effectivePromptDigest,undefined);
  const first=graph.nodes.find(node=>node.id==='first');
  assert.equal(first.assetId,'media-first');assert.equal(first.mujian.archived,true);
  assert.equal(graph.nodes.find(node=>node.id==='edit').mujian.mediaStale,true);
  assert.equal(validateProductionGraph(graph),true);
});

test('invalid proposal leaves manual settings, media and references untouched',()=>{
  const graph=fixture(),video=graph.nodes.find(node=>node.id==='video');
  video.mujian.proposed.incomingEdges=[edge('missing','video')];
  const before=structuredClone(graph);
  assert.throws(()=>applyStudioProposal(graph,'video'),/失效连线|前置素材/);
  assert.deepEqual(graph,before);
  video.mujian.proposed.incomingEdges=[edge('character','other-video')];
  const second=structuredClone(graph);
  assert.throws(()=>applyStudioProposal(graph,'video'),/不属于当前节点/);
  assert.deepEqual(graph,second);
});

test('fresh auto-mode settings remain identical to the accepted definition',()=>{
  const graph=fixture(),video=graph.nodes.find(node=>node.id==='video');
  video.mujian.proposed.settings.video_mode='auto';
  video.mujian.proposed.settings.frame_asset_ids=[];
  const generatedSettings=structuredClone(video.mujian.proposed.settings);
  applyStudioProposal(graph,'video');
  assert.equal(video.mujian.mode,'reference');
  assert.equal(video.settings.video_mode,'auto');
  assert.deepEqual(video.settings,generatedSettings,'Accepting must not alter the settings covered by appliedDefinitionHash');
  assert.equal(video.mujian.appliedDefinitionHash,'new-definition');
});

test('older metadata proposals map missing native modes without retaining stale frame slots',()=>{
  for(const mode of ['reference','first','text','first-last']){
    const graph=fixture(),video=graph.nodes.find(node=>node.id==='video');
    video.mujian.proposed.mujian.mode=mode;
    delete video.mujian.proposed.settings.video_mode;
    applyStudioProposal(graph,'video');
    assert.equal(video.settings.video_mode,mode==='first-last'?'first-last':'auto');
    assert.equal(video.settings.frame_asset_ids,undefined);
  }
});

test('user links keep an old frame available for manual reuse',()=>{
  const graph=fixture();
  graph.edges.push({id:'manual-frame-use',from:'first',to:'manual'});
  applyStudioProposal(graph,'video');
  assert.notEqual(graph.nodes.find(node=>node.id==='first').mujian.archived,true);
  assert.equal(graph.edges.some(item=>item.id==='manual-frame-use'),true);
});

test('legacy prompt-only proposals preserve their original reference mode',()=>{
  const graph=fixture(),video=graph.nodes.find(node=>node.id==='video');
  video.mujian.mode='first-last';
  video.mujian.proposed={settings:{...video.settings,prompt:'新提示词',video_mode:'first-last',frame_asset_ids:['media-first','media-scene']}};
  applyStudioProposal(graph,'video');
  assert.equal(video.mujian.mode,'first-last');
  assert.equal(video.settings.video_mode,'first-last');
  assert.deepEqual(video.settings.frame_asset_ids,['media-first','media-scene']);
  assert.equal(graph.edges.some(item=>item.from==='first'&&item.to==='video'),true);
});
