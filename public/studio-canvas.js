import { applyStudioProposal, bindMedia, resolveInputs } from './studio-contract.js?v=direct-reference-3';

export function studioNodeBody(node,esc,icon){
  const url=typeof node.assetUrl==='string'&&node.assetUrl.startsWith('/uploads/')?node.assetUrl:'';
  return `<div class="studio-timeline-card">${url?`<video src="${esc(url)}" controls preload="metadata" playsinline class="frame-media"></video>`:`<div class="studio-timeline-empty">${icon('video')}<strong>${node.mujian?.role==='final'?'整剧成片':'分集剪辑'}</strong><span>${node.clips?.length||0} ${node.mujian?.role==='final'?'集':'个镜头'} · 按顺序合成</span></div>`}</div>`;
}

export function createStudioCanvas(ctx){
  const {state,api,json,escapeHtml:esc,icon,toast}=ctx;
  const submissions=new Map(),timers=new Map();
  const graph=()=>({nodes:state.nodes,edges:state.edges,groups:state.groups});
  function changed(){ctx.renderNodes();ctx.loadNodeSettings();ctx.save();}
  function bind(node,asset){if(node.mujian){bindMedia(graph(),node.id,asset);if(node.type==='video')node.referenceAssetId=asset.id;}}
  function review(node,host){
    host.querySelector('.studio-node-review')?.remove();
    if(!node.mujian)return;
    const meta=node.mujian;
    if(!meta.needsReview&&!meta.mediaStale)return;
    const banner=document.createElement('section');banner.className='studio-node-review';
    banner.innerHTML=`<strong>剧本或前置素材已更新</strong><p>核对提示词与引用，再决定继续使用当前方案或采用更新方案。</p>${meta.proposed?'<button type="button" data-apply>采用更新方案</button>':''}<button type="button" data-keep>已核对，保留当前方案</button>`;
    host.prepend(banner);
    banner.querySelector('[data-keep]').onclick=()=>{meta.needsReview=false;meta.mediaStale=false;changed();};
    banner.querySelector('[data-apply]')?.addEventListener('click',()=>{
      try{applyStudioProposal(graph(),node.id);delete state.nodeTasks[node.id];changed();}catch(error){toast(error.message);}
    });
  }
  function syncFrames(node){
    if(node.mujian?.mode!=='first-last')return;
    const {refs}=resolveInputs(node,graph(),state.assets,state.characters);
    node.settings.frame_asset_ids=['first','last'].map(slot=>refs.find(r=>r.slot===slot)?.assetId||null);
  }
  function validate(node){
    if(!node.mujian)return;
    if(node.mujian.archived)throw new Error('该节点已不属于当前剧本，请使用新的镜头节点');
    if(node.mujian.needsReview||node.mujian.mediaStale)throw new Error('剧本或素材有变化，请先核对当前节点');
    const {missing}=resolveInputs(node,graph(),state.assets,state.characters);
    if(missing.length)throw new Error('前置素材未就绪：'+missing.map(m=>m.title||m.reason).join('、'));
    syncFrames(node);
  }
  function mount(node,host){
    const refs=resolveInputs(node,graph(),state.assets,state.characters);
    const editorKey=state.project.id+':'+node.id,expanded=host.dataset.timelineEditor===editorKey;
    host.innerHTML=`<div class="studio-timeline-detail"><h3>${esc(node.title)}</h3><p>${node.mujian?.role==='final'?'将所有已完成分集按顺序合为完整影片，使用各集实际时长。':'按镜头顺序剪辑，保留视频原声。可以调整顺序、使用时长与原声。'}</p>${node.assetUrl?`<div class="media-detail-preview"><video src="${esc(node.assetUrl)}" controls preload="metadata" playsinline></video></div><button class="studio-quiet" data-download-output>下载成片</button>`:''}<button type="button" class="studio-quiet" data-edit-timeline aria-expanded="${expanded}" aria-controls="timeline-editor-${esc(node.id)}">编辑与合成</button><section class="studio-timeline-editor" id="timeline-editor-${esc(node.id)}" ${expanded?'':'hidden'}><ol class="studio-edit-clips">${(node.clips||[]).map((clip,i)=>{const source=state.nodes.find(n=>n.id===clip.nodeId),asset=state.assets.find(a=>a.id===source?.assetId);return `<li data-clip="${i}"><div><b>${esc(source?.title||'缺失的镜头')}</b><small>${asset?'视频已就绪':'等待视频生成'}</small></div><button type="button" data-move="-1" ${i===0?'disabled':''} aria-label="上移镜头">↑</button><button type="button" data-move="1" ${i===node.clips.length-1?'disabled':''} aria-label="下移镜头">↓</button>${node.mujian?.role!=='final'?`<label><input type="number" min="0.1" max="60" step="0.1" value="${Number(clip.duration)||4}" data-duration aria-label="镜头使用时长">秒</label><label><input type="checkbox" data-mute ${clip.muteOriginal?'checked':''}>静音</label>`:''}</li>`;}).join('')}</ol><p class="studio-render-status" role="status">${refs.missing.length?'还需准备 '+refs.missing.length+' 项前置视频':'视频已就绪，可以合成'}</p><button class="studio-primary" data-render ${refs.missing.length||node.mujian?.needsReview||node.mujian?.mediaStale?'disabled':''}>${node.mujian?.role==='final'?'合成完整视频':'合成本集'}</button></section></div>`;
    host.querySelector('[data-edit-timeline]').onclick=event=>{
      const panel=host.querySelector('.studio-timeline-editor'),open=panel.hidden;
      panel.hidden=!open;host.dataset.timelineEditor=open?editorKey:'';event.currentTarget.setAttribute('aria-expanded',String(open));
    };
    host.querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>{const i=Number(b.closest('[data-clip]').dataset.clip),j=i+Number(b.dataset.move);[node.clips[i],node.clips[j]]=[node.clips[j],node.clips[i]];ctx.save();mount(node,host);review(node,host);});
    host.querySelectorAll('[data-duration]').forEach(input=>input.onchange=()=>{const value=Number(input.value);if(!(value>0&&value<=60)){toast('镜头使用时长须为 0–60 秒');return;}node.clips[Number(input.closest('[data-clip]').dataset.clip)].duration=value;ctx.save();});
    host.querySelectorAll('[data-mute]').forEach(input=>input.onchange=()=>{node.clips[Number(input.closest('[data-clip]').dataset.clip)].muteOriginal=input.checked;ctx.save();});
    host.querySelector('[data-render]').onclick=()=>render(node,host);
    host.querySelector('[data-download-output]')?.addEventListener('click',()=>ctx.download(state.assets.find(a=>a.id===node.assetId)||{id:node.assetId,name:node.title+'.mp4',url:node.assetUrl,kind:'video'}));
    const pid=state.project.id;
    api(`/api/studio/renders?project_id=${encodeURIComponent(pid)}`).then(jobs=>{if(state.project?.id!==pid||!host.isConnected)return;const job=jobs.filter(j=>j.node_id===node.id).sort((a,b)=>b.created_at-a.created_at)[0];if(job&&['queued','running'].includes(job.status)){showStatus(host,job);poll(job.id,pid,node.id,host);}}).catch(error=>toast(error.message));
  }
  function showStatus(host,job){
    const status=host.querySelector('.studio-render-status');if(status)status.textContent=job.error||job.progress||'正在合成';
    const button=host.querySelector('[data-render]');if(button)button.disabled=['running','queued'].includes(job.status);
  }
  async function render(node,host){
    const key=state.project.id+':'+node.id;if(submissions.get(key)?.busy)return;
    try{
      validate(node);await ctx.flushSave();
      const existing=submissions.get(key),body=existing?.body||{project_id:state.project.id,node_id:node.id,revision:state.project.revision,request_id:crypto.randomUUID()};
      submissions.set(key,{busy:true,body});showStatus(host,{status:'queued',progress:'正在提交合成'});
      const job=await api('/api/studio/renders',json(body));submissions.delete(key);
      poll(job.id,body.project_id,node.id,host);
    }catch(error){const previous=submissions.get(key);if(error.status)submissions.delete(key);else if(previous)previous.busy=false;showStatus(host,{status:'failed',error:error.message});}
  }
  async function poll(tid,pid,nid,host){
    clearTimeout(timers.get(tid));
    if(state.project?.id!==pid)return;
    try{
      const job=await api(`/api/studio/renders/${tid}`);
      if(state.project?.id!==pid)return;
      if(host.isConnected)showStatus(host,job);
      if(['queued','running'].includes(job.status)){timers.set(tid,setTimeout(()=>poll(tid,pid,nid,host),1800));return;}
      timers.delete(tid);
      if(job.status==='succeeded'){
        if(job.asset)ctx.upsertAsset(job.asset);
        if(job.bound&&!state.dirty){await ctx.refreshProject(pid);}
        else toast(job.progress||'合成完成，视频已保存在生成历史');
      }
    }catch(error){if(host.isConnected)showStatus(host,{status:'failed',error:error.message});}
  }
  return {mount,review,bind,syncFrames,validate};
}
