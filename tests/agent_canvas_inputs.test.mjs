import assert from 'node:assert/strict';
import { resolveVideoInput, resolveVideoAssets, materializeVideoAssets } from '../public/node-inputs.js';
import { syncScriptResourceGraph, detachScriptResourceEdges } from '../public/script-resource-graph.js';

function test(name, check) { check(); console.log('PASS:', name); }

test('video reference edges, mentions and generated result snapshots resolve and deduplicate',()=>{
  const target={id:'target',type:'video',settings:{prompt:'参考 @运动',mentions:[{token:'@运动',asset_id:'clip',kind:'video'}]}};
  const source={id:'source',type:'video-asset',assetId:'clip'};
  const generated={id:'generated',type:'video',referenceAssetId:'result',referenceTaskId:'task',settings:{asset_ids:['unrelated-input']}};
  const assets=[{id:'clip',kind:'video'},{id:'result',kind:'video'},{id:'unrelated-input',kind:'image'}];
  const nodes=[target,source,generated],edges=[{from:'source',to:'target'},{from:'generated',to:'target'}];
  assert.deepEqual(resolveVideoAssets(target,nodes,edges,assets).assetIds,['clip','result']);
  target.settings.prompt='移除引用';
  assert.deepEqual(resolveVideoAssets(target,nodes,[],assets).assetIds,[]);
});

test('selecting a video asset creates the correct reusable reference node',()=>{
  const target={id:'target',type:'video',settings:{asset_ids:['clip']}};
  const nodes=[target],edges=[],assets=[{id:'clip',kind:'video',url:'/uploads/clip.mp4'}];
  assert.equal(materializeVideoAssets(target,nodes,edges,assets,[],()=> 'reference'),true);
  assert.equal(nodes[1].type,'video-asset');
  assert.deepEqual(resolveVideoAssets(target,nodes,edges,assets).assetIds,['clip']);
  edges.length=0;
  assert.deepEqual(resolveVideoAssets(target,nodes,edges,assets).assetIds,[]);
});

test('script scene video resources become real video references',()=>{
  const target={id:'target',type:'video',settings:{script_scene_index:1}};
  const script={id:'script',type:'script',script_data:{scenes:[{index:1,resource_ids:['motion']}],resources:[{id:'motion',kind:'video',asset_id:'clip'}]}};
  assert.deepEqual(resolveVideoAssets(target,[target,script],[{from:'script',to:'target'}],[{id:'clip',kind:'video'}]).assetIds,['clip']);
});

test('video audio references resolve from edges and text mentions without becoming images',()=>{
  const video={id:'v',type:'video',settings:{}};
  const audio={id:'a',type:'audio',assetId:'voice'};
  const text={id:'t',type:'prompt',settings:{prompt:'使用 @配音',mentions:[{token:'@配音',asset_id:'voice'}]}};
  const assets=[{id:'voice',kind:'audio'}];
  assert.deepEqual(resolveVideoAssets(video,[video,audio,text],[{from:'a',to:'v'},{from:'t',to:'v'}],assets).assetIds,['voice']);
  assert.equal(resolveVideoAssets(video,[video,audio],[],assets).entries.length,0);
});

test('flow connections cannot override an approved prompt or reference set', () => {
  const video = {id:'video',type:'video',settings:{}};
  const prompt = {id:'prompt',type:'prompt',settings:{prompt:'批准的分镜'}};
  const document = {id:'doc',type:'prompt',settings:{prompt:'内部审阅说明'}};
  const image = {id:'image',type:'asset',assetId:'approved'};
  const unrelated = {id:'unrelated',type:'asset',assetId:'other'};
  const nodes = [video,prompt,document,image,unrelated];
  const edges = [{from:'doc',to:'video',relation:'agent-flow'}, {from:'unrelated',to:'video',relation:'agent-flow'},
    {from:'prompt',to:'video'}, {from:'image',to:'video'}];
  assert.equal(resolveVideoInput(video,nodes,edges).prompt,'批准的分镜');
  assert.deepEqual(resolveVideoAssets(video,nodes,edges,[{id:'approved',kind:'image'},{id:'other',kind:'image'}]).assetIds,['approved']);
});

test('script resource projection keeps actual bindings separate from flow links', () => {
  const script={id:'script',type:'script',script_data:{resources:[{id:'ref',kind:'image',asset_id:'asset'}]}};
  const image={id:'image',type:'asset',assetId:'asset'};
  const flow={from:'image',to:'script',relation:'agent-flow'};
  const edges=[flow], nodes=[image,script];
  syncScriptResourceGraph(nodes,edges,[{id:'asset',kind:'image'}]);
  assert.equal(nodes.length,2);
  assert.equal(edges.length,2);
  assert.equal(edges[1].scriptResourceManaged,true);
  assert.deepEqual(detachScriptResourceEdges(nodes,[flow],[]),[]);
  assert.equal(script.script_data.resources[0].asset_id,'asset');
});

test('script resource projection reuses an existing character image without duplicating its node', () => {
  const script = {id:'script',type:'script',script_data:{resources:[{id:'hero',kind:'image',asset_id:'portrait'}]}};
  const character = {id:'visual_hero',type:'character',assetId:'portrait'};
  const nodes = [character,script], edges = [], assets = [{id:'portrait',kind:'image'}];
  const first = syncScriptResourceGraph(nodes,edges,assets);
  assert.deepEqual(first.createdNodeIds,[]);
  assert.deepEqual(edges.map(edge => [edge.from,edge.to]),[['visual_hero','script']]);
  assert.equal(nodes.length,2);
  assert.equal(syncScriptResourceGraph(nodes,edges,assets).changed,false);
  const removed = edges.splice(0);
  assert.equal(detachScriptResourceEdges(nodes,removed,edges).length,1);
  assert.equal(script.script_data.resources[0].asset_id,'');
  assert.equal(syncScriptResourceGraph(nodes,edges,assets).changed,false);
});

test('existing character input remains manual when it already supplies a script image', () => {
  const script = {id:'script',type:'script',script_data:{resources:[{id:'hero',kind:'image',asset_id:'portrait'}]}};
  const nodes = [{id:'hero',type:'character',assetId:'portrait'},script];
  const edge = {from:'hero',to:'script'}, edges = [edge];
  assert.equal(syncScriptResourceGraph(nodes,edges,[{id:'portrait',kind:'image'}]).changed,false);
  assert.equal(nodes.length,2);
  assert.equal(edges[0],edge);
  assert.equal(edge.scriptResourceManaged,undefined);
});

test('a character node cannot substitute for a video resource', () => {
  const script = {id:'script',type:'script',script_data:{resources:[{id:'clip',kind:'video',asset_id:'video'}]}};
  const nodes = [{id:'invalid-character',type:'character',assetId:'video'},script], edges = [];
  syncScriptResourceGraph(nodes,edges,[{id:'video',kind:'video'}],{makeId:()=>'clip'});
  assert.equal(nodes.length,3);
  assert.equal(nodes[2].type,'video-asset');
  assert.equal(edges[0].from,'clip');
});
