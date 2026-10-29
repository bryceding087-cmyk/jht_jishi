// Execute the production video request handlers with controlled network and
// clock boundaries. No server, provider, database or browser profile is used.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {stoppedTask} from '../public/media-request-stop.js';

const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function section(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}

function harness(){
  const node={id:'clip',type:'video',title:'Clip',settings:{prompt:'Keep video draft',duration:4,resolution:'768p',aspect_ratio:'16:9'}};
  const state={project:{id:'project'},nodes:[node],edges:[],assets:[],characters:[],nodeTasks:{},taskPollers:new Map(),selectedNodeId:'clip'};
  const button={state:'idle'},historyElement={innerHTML:'',querySelectorAll:()=>[]};
  const requests=[],notifications=[],timers=new Map(),replies=new Map();
  let timerId=0,requestId=0,history=[];
  const api=async(url,body)=>{
    requests.push({url,body});
    if(url==='/api/tasks?project_id=project')return history;
    const queue=replies.get(url);assert.ok(queue?.length,'Unexpected request '+url);
    const value=queue.shift();if(value instanceof Error)throw value;
    return value;
  };
  const context=vm.createContext({
    state,stoppedTask,selectedNode:()=>node,api,json:value=>value,
    $:selector=>selector==='#generateBtn'?button:selector==='#taskHistory'?historyElement:null,
    createRequestButton:()=>({setState:(next,options)=>{button.state=next;button.disabled=!!options?.disabled;}}),
    document:{querySelector:()=>null},CSS:{escape:value=>value},
    toast:message=>notifications.push(message),captureSettings(){},studioCanvas:{validate(){}},
    deriveVideoMode:()=> 'text-to-video',resolveVideoInput:()=>({prompt:node.settings.prompt,negative_prompt:''}),
    resolveVideoAssets:()=>({missing:[],entries:[]}),selectedModel:()=>({id:'video-model',model_id:'test-video'}),
    flushSave:async()=>{},crypto:{randomUUID:()=> 'request-'+(++requestId)},
    clearTimeout:id=>timers.delete(id),window:{setTimeout:callback=>{const id=++timerId;timers.set(id,callback);return id;}},
    escapeHtml:value=>String(value??''),statusText:value=>value,taskErrorHtml:()=>'',taskRetryHtml:()=>'',
    describeTaskError:()=>({quotaLimited:false,message:'fixture failure'}),closeModal(){},
  });
  vm.runInContext(
    section('const videoSubmissions =','function taskErrorHtml(task)')+
    section('function updateTaskNode(task,','function renderAssetLibrary()')+
    '\nglobalThis.videoState={videoSubmissions,videoRequests,videoPollVersions};',context);
  const task=(id,status)=>({id,status,project_id:'project',created_at:1,
    params:{node_id:'clip',project_id:'project',prompt:'Keep video draft',model:'test-video',duration:4,resolution:'768p'},
    ...(status==='done'?{video_url:'/uploads/completed.mp4',video_cache_status:'ready'}:{})});
  return {state,node,button,requests,notifications,context,task,
    queue(url,...values){const queue=replies.get(url)||[];queue.push(...values);replies.set(url,queue);},
    start:entry=>entry==='retry'?context.retryTask(task('failed-source','failed')):context.submitTask(),
    stop:()=>context.stopVideoRequest(),
    busy:()=>context.videoGenerationBusy('clip'),
    setHistory:value=>{history=value;},
    runPoll(taskId){const timer=state.taskPollers.get(taskId);assert.ok(timers.has(timer),'Expected a scheduled poll');const callback=timers.get(timer);timers.delete(timer);return callback();},
    request:()=>context.videoState.videoRequests.get('project:clip'),
    submitting:()=>context.videoState.videoSubmissions.has('project:clip'),
  };
}

// The provider finished while the original POST response was held. Stop must
// preserve that result and permanently invalidate the late queued snapshot.
for(const entry of ['submit','retry']){
  const env=harness(),post=deferred(),stop=deferred(),finished=deferred();
  env.queue('/api/tasks',post.promise);env.queue('/api/generation/stop',stop.promise);
  env.queue('/api/tasks/finished-task',finished.promise);
  const submitting=env.start(entry);await settle();
  assert.equal(env.button.state,'running');assert.equal(env.submitting(),true);
  const stopping=env.stop();await settle();assert.equal(env.button.state,'stopping');
  stop.resolve({status:'already_finished',tasks:[{id:'finished-task',status:'done'}]});await settle();
  finished.resolve(env.task('finished-task','done'));await stopping;
  assert.equal(env.request().finished,true);assert.equal(env.submitting(),false);
  assert.equal(env.state.nodeTasks.clip.status,'done');assert.equal(env.button.state,'idle');assert.equal(env.busy(),false);
  post.resolve(env.task('finished-task','queued'));await submitting;
  assert.equal(env.state.nodeTasks.clip.status,'done');
  assert.equal(env.state.nodeTasks.clip.video_url,'/uploads/completed.mp4');
  assert.equal(env.button.state,'idle');assert.equal(env.busy(),false);
  // A late history list is another source of the same old queued snapshot.
  env.setHistory([env.task('finished-task','queued')]);await env.context.loadHistory();
  assert.equal(env.state.nodeTasks.clip.status,'done');assert.equal(env.busy(),false);
  assert.equal(env.node.settings.prompt,'Keep video draft');
}

// A failed stop resumes with a fresh GET. An already in-flight GET must not
// overwrite its completed result, nor may its late failure schedule a new poll.
for(const oldResponse of ['running','error']){
  const env=harness(),old=deferred(),stop=deferred(),recovered=deferred();
  env.state.nodeTasks.clip=env.task('restored-task','running');
  env.queue('/api/tasks/restored-task',old.promise,recovered.promise);
  env.queue('/api/generation/stop',stop.promise);
  await env.context.pollTask('restored-task','clip');const earlier=env.runPoll('restored-task');await settle();
  const stopping=env.stop();await settle();
  stop.reject(Error('stop unavailable'));await settle();
  recovered.resolve(env.task('restored-task','done'));await stopping;
  assert.equal(env.state.nodeTasks.clip.status,'done');assert.equal(env.busy(),false);
  const recoveryTimer=env.state.taskPollers.get('restored-task');
  if(oldResponse==='running')old.resolve(env.task('restored-task','running'));
  else old.reject(Error('old GET disconnected'));
  await earlier;
  assert.equal(env.state.nodeTasks.clip.status,'done');assert.equal(env.button.state,'idle');
  assert.equal(env.state.nodeTasks.clip.video_url,'/uploads/completed.mp4');
  assert.equal(env.state.taskPollers.get('restored-task'),recoveryTimer,'late old poll cannot replace the recovery poll');
}

// A stopped original POST can settle after a replacement request has begun.
// Its finally handler must leave the replacement's submission lock intact.
for(const entry of ['submit','retry'])for(const oldResponse of ['queued','error']){
  const env=harness(),old=deferred(),replacement=deferred(),stop=deferred();
  env.queue('/api/tasks',old.promise,replacement.promise);env.queue('/api/generation/stop',stop.promise);
  const first=env.start(entry);await settle();const firstRequest=env.request();
  const stopping=env.stop();stop.resolve({status:'stopped',tasks:[]});await stopping;
  const second=env.start(entry);await settle();const secondRequest=env.request();
  assert.notEqual(secondRequest.request_id,firstRequest.request_id);
  assert.equal(env.submitting(),true);assert.equal(env.button.state,'running');
  if(oldResponse==='queued')old.resolve(env.task('old-task','queued'));
  else old.reject(Object.assign(Error('cancelled old submission'),{status:409}));
  await first;
  assert.equal(env.request(),secondRequest);assert.equal(env.submitting(),true);
  assert.equal(env.button.state,'running');assert.equal(env.busy(),true);
  assert.equal(env.state.nodeTasks.clip,undefined,'late stopped POST does not attach a task');
  replacement.resolve(env.task('replacement-task','queued'));await second;
  assert.equal(env.submitting(),false);assert.equal(env.state.nodeTasks.clip.id,'replacement-task');
  assert.equal(env.button.state,'running');assert.equal(env.busy(),true);
}

// Historical tasks have no request ID. Stopping one must not match a newer
// task merely because both requests lack that optional field.
{
  const env=harness();
  env.state.nodeTasks.clip=env.task('legacy-task','running');
  env.queue('/api/generation/stop',{status:'stopped',tasks:[{id:'legacy-task'}]});
  await env.stop();
  assert.equal(env.state.nodeTasks.clip.status,'cancelled');
  const next={...env.task('agent-next-task','running'),created_at:2};
  env.context.updateTaskNode(next,'clip');
  assert.equal(env.state.nodeTasks.clip.status,'running');
  assert.equal(stoppedTask(env.state.nodeTasks.clip),false);
  env.setHistory([next]);await env.context.loadHistory();
  assert.equal(env.state.nodeTasks.clip.status,'running');
  assert.equal(env.button.state,'running');
}

console.log('PASS: canvas video submit/retry already_finished preserves results; late POST/history/GET cannot roll back state; failed-stop recovery, replacement requests and legacy task IDs stay isolated.');
