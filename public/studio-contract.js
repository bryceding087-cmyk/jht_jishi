// Portable contract: no network, credentials, browser globals, or automatic generation.
export const VIDEO_MODES = {reference:'多图参考',first:'首帧驱动','first-last':'首尾帧',text:'纯文本'};
export const JHT_LIMITS = {nodes:1600,edges:10000,groups:100,groupMembers:400,prompt:4000,imageReferences:10,clips:80,seconds:1200};
export const nodePrompt = n => n.type==='video' ? n.settings?.prompt||'' : n.image_settings?.prompt||n.settings?.prompt||'';
export const outputKind = n => ['shot','edit','final'].includes(n.mujian?.role)?'video':n.mujian?.role==='script'?'text':'image';
const error = message => {throw new Error(message);};
const imageKind = kind => ['image','prop'].includes(kind);

export function validateProductionGraph(graph) {
  const ids=new Set(),grouped=new Set();
  for(const n of graph.nodes){if(!n.id||ids.has(n.id))error('画布节点编号重复或为空');ids.add(n.id);}
  const incoming=new Map(graph.nodes.map(n=>[n.id,[]]));
  for(const e of graph.edges){if(!ids.has(e.from)||!ids.has(e.to)||e.from===e.to)error('画布有失效连线');incoming.get(e.to).push(e.from);}
  for(const n of graph.nodes){
    const inputs=n.mujian?.inputs||[];
    if(new Set(inputs.map(r=>r.nodeId)).size!==inputs.length)error('同一输入不能重复占据参考图序号');
    for(const input of inputs){
      const source=graph.nodes.find(s=>s.id===input.nodeId);
      if(!source||!incoming.get(n.id).includes(input.nodeId))error('前置素材必须对应真实画布连线');
      if(outputKind(source)!==input.kind)error('前置素材类型不匹配');
    }
  }
  const visiting=new Set(),visited=new Set();
  const visit=id=>{if(visiting.has(id))error('画布素材依赖存在循环');if(visited.has(id))return;visiting.add(id);incoming.get(id).forEach(visit);visiting.delete(id);visited.add(id);};
  ids.forEach(visit);
  for(const g of graph.groups){for(const id of g.node_ids){if(!ids.has(id)||grouped.has(id))error('分组引用不存在的节点或重复归组');grouped.add(id);}}
  return true;
}

export function inputSpecs(node,graph) {
  const inputs=(node.mujian?.inputs||[]).map(ref=>({...ref})),known=new Set(inputs.map(ref=>ref.nodeId));
  if(node.type==='timeline')return inputs;
  const kinds={asset:'image',prop:'image',character:'image',audio:'audio','video-asset':'video',video:'video'};
  for(const edge of graph.edges){
    const source=graph.nodes.find(n=>n.id===edge.from),kind=kinds[source?.type];
    if(edge.to!==node.id||edge.relation==='agent-flow'||!kind||known.has(source.id)||(node.type!=='video'&&kind!=='image'))continue;
    inputs.push({nodeId:source.id,kind,role:source.title||'参考素材',additional:true});known.add(source.id);
  }
  return inputs;
}

// A reference points to a node, even before that node has generated its media.
export function mentionReference(mention,nodes,assets,characters=[]) {
  const source=nodes.find(n=>n.id===mention.node_id);
  const assetId=source?(source.type==='video'?source.referenceAssetId||source.assetId:source.assetId)||(source.type==='character'?characters.find(c=>c.id===source.characterId)?.asset_id:''):mention.node_id?'':mention.asset_id;
  const asset=assets.find(a=>a.id===assetId&&!a.deleted_at);
  return {token:mention.token,nodeId:mention.node_id,assetId:asset?.id||'',name:mention.name||source?.title,kind:mention.kind,url:asset?.url,duration:asset?.duration,pending:!asset&&!['text','script'].includes(mention.kind)};
}

export function assertReferenceLink(graph,from,to) {
  const visit=[to],seen=new Set();
  while(visit.length){const id=visit.pop();if(id===from)error('不能引用自己或下游节点，以免形成循环引用');if(seen.has(id))continue;seen.add(id);for(const e of graph.edges)if(e.from===id&&e.relation!=='agent-flow')visit.push(e.to);}
}

export function detachReference(graph,node,sourceId,assetId='') {
  graph.edges=graph.edges.filter(e=>!(e.from===sourceId&&e.to===node.id&&e.relation!=='agent-flow'));
  if(node.mujian)node.mujian.inputs=(node.mujian.inputs||[]).filter(r=>r.nodeId!==sourceId);
  const settings=node.type==='video'?node.settings:node.type==='audio'?node.audio_settings:node.image_settings;
  if(!settings)return;
  const removed=(settings.mentions||[]).filter(m=>sourceId&&m.node_id===sourceId||!m.node_id&&assetId&&m.asset_id===assetId);
  // Longest first so removing a similarly named reference cannot damage another token.
  const all=(settings.mentions||[]).filter(m=>m.token).sort((a,b)=>b.token.length-a.token.length);
  if(all.length){const pattern=new RegExp(all.map(m=>m.token.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|'),'g');settings.prompt=String(settings.prompt||'').replace(pattern,token=>removed.some(m=>m.token===token)?'':token);}
  settings.mentions=(settings.mentions||[]).filter(m=>!removed.includes(m));
  for(const key of ['asset_ids','reference_asset_ids','reference_order'])if(settings[key])settings[key]=settings[key].filter(id=>id!==assetId);
  if(settings.frame_asset_ids)settings.frame_asset_ids=settings.frame_asset_ids.map(id=>id===assetId?null:id);
}

// Apply text, reference slots and managed edges together. A rejected proposal
// must leave the editable canvas, old media and manual links intact.
export function applyStudioProposal(graph,nodeId,{now=Date.now()/1000}={}) {
  const original=graph.nodes.find(node=>node.id===nodeId);
  if(!original?.mujian?.proposed)error('该节点没有可采用的更新方案');
  const draft=structuredClone(graph),node=draft.nodes.find(item=>item.id===nodeId),meta=node.mujian,proposal=meta.proposed;
  const oldFrameIds=new Set((meta.inputs||[]).map(ref=>ref.nodeId));
  const metaFields=['inputs','mode','shots','plannedSeconds','sources','sceneKey','episode','index','dialogue','definitionHash','appliedDefinitionHash'];
  for(const key of ['settings','image_settings','clips','aspect_ratio'])if(proposal[key]!=null)node[key]=structuredClone(proposal[key]);
  if(proposal.mujian){
    for(const key of metaFields)if(Object.hasOwn(proposal.mujian,key))meta[key]=structuredClone(proposal.mujian[key]);
  }
  if(proposal.incomingEdges!=null){
    if(!Array.isArray(proposal.incomingEdges)||proposal.incomingEdges.some(edge=>edge.to!==nodeId||edge.studio_run_id!==meta.runId))error('更新方案的素材连线不属于当前节点');
    draft.edges=draft.edges.filter(edge=>!(edge.to===nodeId&&edge.studio_run_id===meta.runId));
    const edgeKey=edge=>JSON.stringify([edge.from,edge.to,edge.slot||'']);
    const existing=new Set(draft.edges.map(edgeKey));
    for(const edge of proposal.incomingEdges){if(!existing.has(edgeKey(edge))){draft.edges.push(structuredClone(edge));existing.add(edgeKey(edge));}}
  }
  // Keep the generated settings byte-for-byte compatible with its definition
  // hash. Only older proposals without native mode/slot fields need migration.
  if(node.type==='video'&&proposal.mujian?.mode){
    node.settings??={};
    if(!Object.hasOwn(proposal.settings||{},'video_mode'))node.settings.video_mode=meta.mode==='first-last'?'first-last':'auto';
    if(meta.mode!=='first-last'&&!Object.hasOwn(proposal.settings||{},'frame_asset_ids'))delete node.settings.frame_asset_ids;
  }
  if(node.assetId)meta.previousMedia=[...(meta.previousMedia||[]),{assetId:node.assetId,assetUrl:node.assetUrl||''}];
  for(const key of ['assetId','assetUrl','referenceAssetId','referenceTaskId','image_task_id','taskId'])delete node[key];
  node.studioOutputAfter=now;
  if(meta.promptOptimization?.status==='proposed'){
    meta.promptOptimization.status='applied';
    delete meta.promptOptimization.effectivePromptDigest;delete meta.promptOptimization.previousApplied;
  }
  meta.needsReview=false;meta.mediaStale=false;delete meta.proposed;
  const seen=new Set(),pending=[nodeId];
  while(pending.length){
    const from=pending.pop();
    for(const edge of draft.edges.filter(edge=>edge.from===from)){
      if(seen.has(edge.to))continue;seen.add(edge.to);pending.push(edge.to);
      const target=draft.nodes.find(item=>item.id===edge.to);
      if(target?.assetId&&target.mujian)target.mujian.mediaStale=true;
    }
  }
  for(const frame of draft.nodes){
    if(!oldFrameIds.has(frame.id)||frame.mujian?.runId!==meta.runId||!['firstFrame','lastFrame'].includes(frame.mujian?.role))continue;
    const used=draft.nodes.some(target=>!target.mujian?.archived&&(target.mujian?.inputs||[]).some(ref=>ref.nodeId===frame.id))||draft.edges.some(edge=>edge.from===frame.id&&edge.relation!=='agent-flow'&&!draft.nodes.find(target=>target.id===edge.to)?.mujian?.archived);
    if(!used)frame.mujian.archived=true;
  }
  validateProductionGraph(draft);
  for(const updated of draft.nodes){
    const target=graph.nodes.find(item=>item.id===updated.id);
    for(const key of Object.keys(target))if(!Object.hasOwn(updated,key))delete target[key];
    Object.assign(target,updated);
  }
  graph.edges.splice(0,graph.edges.length,...draft.edges);
  return original;
}

export function isHiddenStudioEdge(edge,nodes) {
  return edge.hidden===true||nodes.some(n=>n.mujian&&['video','timeline'].includes(n.type)&&(n.id===edge.to||n.id===edge.from));
}

// Audio generation/import uses the existing native audio nodes. Group these in
// a Studio canvas without inventing voice/BGM jobs or moving user-made groups.
export function groupStudioAudio(graph) {
  const run=graph.nodes.find(n=>n.mujian?.runId)?.mujian.runId;
  if(!run)return false;
  const owned=new Set(graph.groups.flatMap(g=>g.node_ids));
  const audio=graph.nodes.filter(n=>n.type==='audio'&&!owned.has(n.id));
  if(!audio.length)return false;
  const id='studio-audio-'+run;
  let group=graph.groups.find(g=>g.id===id);
  if((group?.node_ids.length||0)+audio.length>JHT_LIMITS.groupMembers||!group&&graph.groups.length>=JHT_LIMITS.groups)return false;
  if(!group){group={id,title:'声音素材',color:'#15b9d0',node_ids:[]};graph.groups.push(group);}
  const existing=graph.nodes.filter(n=>group.node_ids.includes(n.id));
  const left=existing.length?Math.min(...existing.map(n=>n.x)):80;
  const top=existing.length?Math.max(...existing.map(n=>n.y))+300:Math.max(80,...graph.nodes.filter(n=>n.type!=='audio'&&n.x<1120).map(n=>n.y+600));
  audio.forEach((node,i)=>{node.x=left+(i%2)*460;node.y=top+Math.floor(i/2)*300;group.node_ids.push(node.id);});
  return true;
}

// JHT must supply its authorized current asset list; model-declared IDs are never media.
export function resolveInputs(node,graph,assets,characters=[]) {
  const refs=[],missing=[];
  for(const input of inputSpecs(node,graph)){
    const source=graph.nodes.find(n=>n.id===input.nodeId);
    const linked=graph.edges.some(e=>e.from===input.nodeId&&e.to===node.id&&e.relation!=='agent-flow');
    const aid=source?.assetId||source?.referenceAssetId||(source?.type==='character'?characters.find(c=>c.id===source.characterId)?.asset_id:'');
    const asset=assets.find(a=>a.id===aid&&!a.deleted_at);
    const valid=asset&&(input.kind==='image'?imageKind(asset.kind):asset.kind===input.kind);
    if(!linked||!valid||source.mujian?.mediaStale||source.mujian?.needsReview||source.mujian?.archived){missing.push({nodeId:input.nodeId,title:source?.title||input.nodeId,reason:!linked?'引用已断开':!valid?'尚未生成或未绑定有效素材':'素材变化后待核对'});continue;}
    if(!input.additional||!refs.some(r=>r.assetId===asset.id))refs.push({...input,assetId:asset.id,asset});
  }
  // Two distinct roles sharing one image would change the platform's image numbering.
  if(new Set(refs.map(r=>r.assetId)).size!==refs.length)missing.push({reason:'不同参考位绑定了同一素材，请分别核对，避免图片序号错位'});
  if(['asset','prop','character'].includes(node.type))for(const id of node.image_settings?.reference_asset_ids||[]){
    const asset=assets.find(a=>a.id===id&&imageKind(a.kind)&&!a.deleted_at);
    if(!asset)missing.push({reason:'添加的参考图已失效'});
    else if(!refs.some(r=>r.assetId===id))refs.push({kind:'image',additional:true,assetId:id,asset});
  }
  return {refs,missing};
}

export function nodeReadiness(node,graph,assets=[]) {
  if(node.mujian?.role==='script')return {state:'document',missing:[]};
  const resolved=resolveInputs(node,graph,assets);
  if(node.mujian?.needsReview||node.mujian?.mediaStale)return {state:'review',...resolved};
  const media=assets.find(a=>a.id===node.assetId);
  if(media&&(outputKind(node)==='image'?imageKind(media.kind):media.kind==='video'))return {state:'bound',...resolved};
  return {state:resolved.missing.length?'blocked':'ready',...resolved};
}

export function compatibilityReport(graph,limits=JHT_LIMITS) {
  const blockers=[];
  for(const field of ['nodes','edges','groups'])if(graph[field].length>limits[field])blockers.push(`jht 当前${{nodes:'节点',edges:'连线',groups:'分组'}[field]}上限 ${limits[field]}，本画布有 ${graph[field].length}。接入前需扩容；不会删减剧集。`);
  for(const g of graph.groups)if(g.node_ids.length>limits.groupMembers)blockers.push(`${g.title}有 ${g.node_ids.length} 个节点，超过单组 ${limits.groupMembers} 上限。`);
  for(const n of graph.nodes){
    if(['asset','prop','video'].includes(n.type)&&nodePrompt(n).length>limits.prompt)blockers.push(`${n.title}提示词为 ${nodePrompt(n).length} 字，超过 jht 的 ${limits.prompt} 字上限；请编辑精简。`);
    if(n.image_settings&&(n.mujian?.inputs||[]).length>limits.imageReferences)blockers.push(`${n.title}图片参考超过 ${limits.imageReferences} 张，请调整生成方式。`);
    if(n.type==='timeline'&&n.mujian?.role!=='final'){
      if(n.clips.length>limits.clips)blockers.push(`${n.title}超过 ${limits.clips} 个片段，需分段合成支持。`);
      if(n.clips.reduce((s,c)=>s+c.duration,0)>limits.seconds)blockers.push(`${n.title}预计超过 ${limits.seconds/60} 分钟的当前合成上限。`);
      if(!['16:9','9:16','1:1'].includes(n.aspect_ratio))blockers.push(`${n.title}的 ${n.aspect_ratio} 尚未接入 jht 剪辑渲染器。`);
    }
  }
  return {compatible:!blockers.length,blockers,limits,counts:{nodes:graph.nodes.length,edges:graph.edges.length,groups:graph.groups.length},hostExtensions:['图片节点提交前检查全部前置素材','首尾帧按输入槽位解析真实素材 ID','时间线节点接入用户触发的剪辑渲染','接入幕间对话及任务状态接口']};
}

export function generationRequest(nodeId,graph,assets,{projectId,model,requestId}={}) {
  const node=graph.nodes.find(n=>n.id===nodeId);
  if(!node||!['asset','prop','video'].includes(node.type))error('请选择图片或视频生成节点');
  if(!projectId||!requestId||!model?.id)error('请由用户选择模型，并提供项目与本次请求编号');
  if(node.mujian?.needsReview||node.mujian?.mediaStale)error('本节点有变更待核对，请先确认提示词和引用');
  const {refs,missing}=resolveInputs(node,graph,assets);
  if(missing.length)error('前置素材未就绪：'+missing.map(m=>m.title||m.reason).join('、'));
  const prompt=nodePrompt(node),settings=node.type==='video'?node.settings:node.image_settings;
  if(!prompt.trim()||prompt.length>4000)error('生成提示词必须为 1–4000 字，不会自动截断');
  const kind=node.type==='video'?'video':'image',caps=model.capabilities||{};
  if(model.kind!==kind)error('所选模型类型与节点不一致');
  if(caps.aspect_ratios?.length&&!caps.aspect_ratios.includes(settings.aspect_ratio))error('模型不支持当前画面比例');
  if(kind==='image'){
    if(refs.length>10||Number.isFinite(caps.max_reference_images)&&refs.length>caps.max_reference_images)error('参考图片数量超过模型或接口上限');
    return {path:'/api/images/generations',body:{project_id:projectId,node_id:node.id,name:node.title,prompt,model_config_id:model.id,request_id:requestId,aspect_ratio:settings.aspect_ratio,size:settings.size||'2K',count:1,watermark:false,asset_kind:node.type==='prop'?'prop':'image',reference_asset_ids:refs.map(r=>r.assetId)}};
  }
  const mode=node.mujian.mode==='first-last'?'first-last':refs.length?'image-to-video':'text-to-video';
  if(caps.modes?.length&&!caps.modes.includes(mode))error('所选模型不支持本镜头生成模式');
  if(caps.durations?.length&&!caps.durations.includes(settings.duration))error('模型不支持该镜头时长，请调整或拆镜');
  if(caps.resolutions?.length&&!caps.resolutions.includes(settings.resolution))error('模型不支持当前分辨率');
  if(Number.isFinite(caps.max_images)&&refs.length>caps.max_images)error('参考图数量超过模型上限');
  if(mode==='first-last'&&(refs.length!==2||refs[0].slot!=='first'||refs[1].slot!=='last'))error('首尾帧必须各有一张，并按首帧、尾帧排列');
  if((settings.negative_prompt||'').length>1000)error('避免项超过接口 1000 字上限');
  return {path:'/api/tasks',body:{project_id:projectId,node_id:node.id,prompt,negative_prompt:settings.negative_prompt||'',mode,model:model.model_id,model_config_id:model.id,request_id:requestId,aspect_ratio:settings.aspect_ratio,duration:settings.duration,resolution:settings.resolution||'768p',asset_ids:refs.map(r=>r.assetId)}};
}

export function renderRequest(nodeId,graph,assets,projectId) {
  const node=graph.nodes.find(n=>n.id===nodeId);
  if(node?.type!=='timeline'||!projectId)error('请选择本项目的剪辑节点');
  if(node.mujian?.role==='final')error('整剧成片请使用宿主 concat-episodes 适配器，不能把长集数传入单镜头渲染接口');
  if(node.mujian?.needsReview||node.mujian?.mediaStale)error('剪辑方案变化后需重新核对');
  const {refs,missing}=resolveInputs(node,graph,assets);
  if(missing.length)error('视频素材未就绪，不能合成成片');
  if(!node.clips.length||node.clips.length>80)error('当前 jht 渲染器只支持 1–80 个片段');
  if(!['16:9','9:16','1:1'].includes(node.aspect_ratio))error('当前 jht 渲染器不支持此画幅');
  const clips=node.clips.map(c=>{
    const ref=refs.find(r=>r.nodeId===c.nodeId);
    if(!ref||!Number.isFinite(ref.asset.duration)||ref.asset.duration<=0)error('需先探测每个视频的实际时长');
    if(!Number.isFinite(c.duration)||c.duration<=0||c.duration>60)error('单段剪辑时长必须为 0–60 秒');
    if(c.duration>ref.asset.duration+.15)error('实际视频短于剪辑预算，请调整时长或补拍');
    return {asset_id:ref.assetId,duration:c.duration,mute_original:c.muteOriginal===true};
  });
  if(clips.reduce((s,c)=>s+c.duration,0)>1200)error('当前 jht 合成上限为 20 分钟');
  // Dialogue cues have no fabricated timestamps; subtitle timing is a separate authoring step.
  return {project_id:projectId,title:node.title,aspect_ratio:node.aspect_ratio,clips,subtitles:[],audio:[],brand:''};
}

// Called inside JHT after an authorized library lookup / successful task. Does not submit tasks.
export function bindMedia(graph,nodeId,asset) {
  const node=graph.nodes.find(n=>n.id===nodeId);
  if(!node||!asset?.id)error('节点或素材不存在');
  if(outputKind(node)==='image'?!imageKind(asset.kind):asset.kind!=='video')error('素材类型与节点不匹配');
  const changed=node.assetId!==asset.id;
  if(changed&&node.assetId)node.mujian.previousMedia=[...(node.mujian.previousMedia||[]),{assetId:node.assetId,assetUrl:node.assetUrl||''}];
  node.assetId=asset.id;node.assetUrl=asset.url||'';node.mujian.mediaStale=false;
  if(changed){
    const visited=new Set(),walk=id=>{for(const e of graph.edges.filter(e=>e.from===id)){if(visited.has(e.to))continue;visited.add(e.to);const child=graph.nodes.find(n=>n.id===e.to);if(child?.assetId&&child.mujian)child.mujian.mediaStale=true;walk(e.to);}};walk(node.id);
  }
  return node;
}

export function prepareNativeGraph(graph,assets) {
  const out=structuredClone(graph);
  for(const node of out.nodes.filter(n=>n.type==='video')){
    const {refs}=resolveInputs(node,out,assets);
    node.settings.reference_order=refs.map(r=>r.assetId);
    node.settings.asset_ids=[];
    if(node.mujian.mode==='first-last')node.settings.frame_asset_ids=['first','last'].map(slot=>refs.find(r=>r.slot===slot)?.assetId||null);
  }
  return out;
}
