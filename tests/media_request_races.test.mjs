import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {beginMediaRequest,requestVersion,stoppedTask,stopMediaRequest} from '../public/media-request-stop.js';

const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const source=name=>readFileSync(new URL('../public/'+name,import.meta.url),'utf8').replace(/\r\n/g,'\n');
function section(text,start,end){const a=text.indexOf(start),b=text.indexOf(end,a);assert.ok(a>=0&&b>a);return text.slice(a,b);}

// The stop acknowledgment can report that the task completed before it arrived.
// Keep that result, invalidate the old POST, and resume only after stopping ends.
for(const status of ['already_finished','completed']) {
  const asset={id:'retained'},owner={status:'running',asset,submitting:true,uncertain:true};
  beginMediaRequest(owner);
  const result={status,tasks:[{id:'done-task',status:'succeeded'}]};
  let resumed;
  await stopMediaRequest(owner,{api:async()=>result,json:value=>value,target:{kind:'audio',request_id:'original'},render(){},resume:value=>{
    assert.equal(owner.stopping,false);assert.equal(owner.submitting,false);resumed=value;
  }});
  assert.equal(owner.status,'succeeded');assert.equal(owner.asset,asset);
  assert.equal(owner.requestVersion,2);assert.equal(owner.uncertain,false);
  assert.equal(resumed,result);
}

// A new request invalidates any old stop's finally handler, even if the new
// request itself is now stopping. Failed stops resume the original request.
{
  const owner={status:'running'},pending=deferred();beginMediaRequest(owner);
  let renders=0;
  const stopped=stopMediaRequest(owner,{api:()=>pending.promise,json:value=>value,target:{},render:()=>renders++});
  beginMediaRequest(owner);owner.stopping=true;
  pending.resolve({status:'stopped'});await stopped;
  assert.equal(owner.stopping,true);assert.equal(owner.status,'running');assert.equal(renders,1);
  owner.stopping=false;let resumed=0;
  await stopMediaRequest(owner,{api:async()=>{throw Error('offline');},json:value=>value,target:{},render(){},resume:()=>{
    assert.equal(owner.stopping,false);resumed++;
  }});
  assert.equal(resumed,1);assert.equal(owner.status,'running');assert.match(owner.error,/offline/);
}

function inlineHarness() {
  const text=source('inline-node-media.js'),post=deferred(),stop=deferred(),get=deferred();
  const urls=[],bound=[],timers=[];
  const node={id:'sound',type:'audio',title:'Sound',audio_task_id:'old-task',audio_settings:{prompt:'Preserved prompt',name:'Sound'}};
  const entry={node,projectId:'project',status:'succeeded',modelId:'audio-model',pollErrors:0};
  let flush=async()=>{};
  const context=vm.createContext({
    beginMediaRequest,requestVersion,stoppedTask,stopMediaRequest,
    valid:()=>true,busy:item=>item.stopping||item.submitting||['running','queued'].includes(item.status),
    kindFor:()=> 'audio',settingsKey:()=> 'audio_settings',taskKey:()=> 'audio_task_id',
    currentTaskId:item=>item.activeTaskId!==undefined?item.activeTaskId:item.node.audio_task_id,
    upstream:()=>'',expandAssetMentions:value=>value,references:()=>[],
    state:{nodes:[node],assets:[]},draft:(item,patch)=>Object.assign(item.node.audio_settings,patch),
    render(){},json:value=>value,crypto:{randomUUID:()=> 'new-request'},
    api:(url,body)=>{urls.push(url);return url==='/api/generation/stop'?stop.promise:body?post.promise:get.promise;},
    ctx:{save(){},flushSave:()=>flush(),upsertAsset(){},bindAsset:(node,asset)=>bound.push(asset.id)},
    clearTimeout(){},setTimeout:fn=>{timers.push(fn);return timers.length;},
  });
  vm.runInContext(section(text,'  async function stop(entry)','  function imageOutput(entry)')+
    section(text,'  async function generate(entry)','  function mount(node,host)'),context);
  return {entry,node,urls,bound,timers,post,stop,get,context,setFlush:fn=>{flush=fn;}};
}

// A rejected bind cannot record a successful application. Repair old completion
// markers without overwriting a manually chosen replacement.
{
  const env=inlineHarness(),asset={id:'generated',url:'/uploads/generated.png'};
  env.context.ctx.bindAsset=()=>false;
  await assert.rejects(env.context.accept(env.entry,{status:'succeeded',asset}),/尚未绑定/);
  assert.equal(env.entry.appliedId,undefined);
  assert.equal(env.node.audio_settings.last_result_asset_id,undefined);
  env.entry.appliedId=asset.id;env.node.audio_settings.last_result_asset_id=asset.id;
  let binds=0;
  env.context.ctx.bindAsset=(node,value)=>{binds++;node.assetId=value.id;node.assetUrl=value.url;return true;};
  await env.context.accept(env.entry,{status:'succeeded',asset});
  assert.equal(binds,1);assert.equal(env.node.assetId,asset.id);
  env.node.assetId='manual';env.node.assetUrl='/uploads/manual.png';
  await env.context.accept(env.entry,{status:'succeeded',asset});
  assert.equal(binds,1);assert.equal(env.node.assetId,'manual');
}

// POST returns while stop is pending. If stopping fails, recover its new task,
// never poll/apply the node's previous task ID.
{
  const env=inlineHarness(),started=env.context.generate(env.entry);await settle();
  const stopping=env.context.stop(env.entry);await settle();
  env.post.resolve({id:'new-task',status:'queued'});await started;
  assert.equal(env.node.audio_task_id,'old-task');assert.equal(env.entry.activeTaskId,'new-task');
  env.stop.reject(Error('temporary stop failure'));await stopping;await settle();
  assert.equal(env.urls.at(-1),'/api/audio/generations/new-task');
  env.get.resolve({id:'new-task',status:'succeeded',asset:{id:'new-asset'}});await settle();
  assert.equal(env.node.audio_task_id,'new-task');assert.equal(env.entry.status,'succeeded');
  assert.deepEqual(env.bound,['new-asset']);
}

// An old result's deferred save cannot overwrite the next request's status.
{
  const env=inlineHarness(),saved=deferred();beginMediaRequest(env.entry);
  env.setFlush(()=>saved.promise);
  const accepting=env.context.accept(env.entry,{id:'done',status:'succeeded',asset:{id:'old-asset'}});
  beginMediaRequest(env.entry);env.entry.status='queued';env.entry.submitting=false;
  saved.resolve();await accepting;
  assert.equal(env.entry.status,'queued');
}

function audioHarness() {
  const text=source('audio-generation.js'),saved=deferred(),post=deferred(),stop=deferred();
  const assets=[];
  const session={model:{api_key_set:true},name:'Voice',prompt:'Keep this prompt',references:[],speaker:'',
    modelId:'audio-model',mode:'voice',audioConfig:{},options:{onTask:()=>saved.promise},status:''};
  const context=vm.createContext({
    beginMediaRequest,requestVersion,stoppedTask,stopMediaRequest,
    active:()=>true,busy:item=>item.stopping||item.submitting||['queued','running'].includes(item.status),
    api:(url,body)=>url==='/api/generation/stop'?stop.promise:post.promise,
    json:value=>value,requestId:()=> 'audio-request',render(){},poll(){},
    ctx:{onAssetCreated:asset=>assets.push(asset.id)},
  });
  vm.runInContext(section(text,'  async function acceptTask(session','  async function poll(session')+
    section(text,'  async function stop(session','  async function open(options'),context);
  return {session,saved,post,stop,context,assets};
}

// Stop during an application's async onTask save must invalidate the callback
// continuation, so its completed response never binds a stopped result.
{
  const env=audioHarness(),started=env.context.generate(env.session);
  env.post.resolve({id:'late-audio',status:'succeeded',asset:{id:'late-audio-asset'}});await settle();
  const stopping=env.context.stop(env.session);
  env.stop.resolve({status:'stopped',tasks:[]});await stopping;
  env.saved.resolve();await started;
  assert.equal(env.session.status,'cancelled');assert.equal(env.session.asset,null);
  assert.deepEqual(env.assets,[]);assert.equal(env.session.prompt,'Keep this prompt');
}

console.log('PASS: completed-stop result retention, stale stop isolation, failed-stop recovery of latest inline task, delayed save isolation and audio onTask cancellation.');

// A failed stop starts a fresh progress query. Earlier GETs must not roll that
// recovered result back to a stale running state or restart the polling loop.
for(const kind of ['image','audio']) {
  const text=source(kind+'-generation.js'),old=deferred(),recovered=deferred(),failed=deferred();
  const replies=[old,recovered],owner={status:'running',taskId:'task',pollFailures:0,pollErrors:0};
  const timers=[];
  const context=vm.createContext({
    active:()=>true,requestVersion,busy:item=>['running','queued'].includes(item.status),
    api:()=>replies.shift().promise,acceptTask:async(item,task)=>{item.status=task.status;item.asset=task.asset;},
    clearTimeout(){},setTimeout:callback=>{timers.push(callback);return timers.length;},render(){},
  });
  vm.runInContext(section(text,'  async function poll(session)',kind==='image'?'  async function useAsset(session)':'  async function stop(session)'),context);
  const earlier=context.poll(owner);
  const stopping=stopMediaRequest(owner,{api:()=>failed.promise,json:value=>value,target:{kind,task_id:'task'},render(){},resume:()=>context.poll(owner)});
  failed.reject(Error('stop offline'));await stopping;
  recovered.resolve({status:'succeeded',asset:{id:'completed'}});await settle();
  assert.equal(owner.status,'succeeded');
  old.resolve({status:'running'});await earlier;
  assert.equal(owner.status,'succeeded');assert.equal(owner.asset.id,'completed');assert.equal(timers.length,0);
}
console.log('PASS: stale image/audio GET responses cannot undo recovery after a failed stop.');
