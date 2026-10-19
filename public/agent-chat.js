import { validateReferenceVideo, videoReferenceCover } from './reference-video.js?v=1';
import { userStorageKey } from './session.js?v=market-sdk-3';
import { icon } from './iconography.js?v=library-toggle-1';
import { renderMarkdown } from './markdown.js?v=agent-docs-1';
import { attachAssetMentions } from './asset-mentions.js?v=reference-sync-2';
import { createHomePreferences } from './home-preferences.js?v=parameter-escape-8';
import { createVisualStylePicker } from './visual-style-picker.js?v=4';
import { normalizeContentError } from './task-errors.js?v=content-rejection-3';
import { withLoading } from './loading-state.js?v=1';

const PHASES = {story_review:'导演检查',analysis:'时长分析',creative:'创意设计',assets:'参考素材',storyboard:'分镜规划',music:'声音与配乐',video:'视频生成',postproduction:'成片合成',review:'成片检查',delivery:'交付完成'};
const STATUS = {running:'正在制作',waiting_tool:'正在生成',waiting_user:'等待确认',paused:'已暂停',blocked:'需要处理',completed:'已完成',cancelled:'已取消'};
const AGENT_LOADER = '<span class="aurora-loader agent-loading" role="status" aria-label="正在处理"><span class="aurora-loader__halo" aria-hidden="true"></span><span class="aurora-loader__rotor" aria-hidden="true"></span><span class="aurora-loader__core" aria-hidden="true"></span></span>';
const TITLES = {'project-context':'创作上下文',director:'导演','story-polishing':'故事润色','model-budget':'模型与费用','story-analysis':'内容与时长','creative-design':'创意设计','visual-assets':'视觉素材','storyboard-planning':'分镜规划','prompt-composition':'提示词','video-generation':'视频生成','voice-production':'音色与配音','music-production':'整片配乐','video-editing':'视频合成','quality-review':'质量检查','canvas-management':'画布管理','revision-recovery':'修改与恢复'};

export function createAgentChat({api, json, escapeHtml: esc, toast, onModels, beforeStart, onProjectSnapshot, onConversationSnapshot, onPreviewAsset, onSaveAsset}) {
  const root = document.getElementById('agentView');
  document.addEventListener('asset-library-change',({detail:asset})=>{
    root.querySelectorAll('.agent-save-library').forEach(button=>{
      if(button.libraryAsset?.id!==asset.id)return;
      Object.assign(button.libraryAsset,asset);
      button.dataset.librarySaved=String(!!asset.in_library);
      button.disabled=!!asset.in_library||button.getAttribute('aria-busy')==='true';
      button.innerHTML=icon('library')+(asset.in_library?'已保存至资产库':'保存至资产库');
    });
  });
  let run = null, capabilities = null, cursor = 0, polling = null, visible = false, busy = false, files = [], mode = 'create', initialized = false;
  const prefs = {aspect_ratio:'16:9',style:'',visual_style:null,scope:'full_video',voice:true,music:true,subtitles:true,canvas:false,models:{}};
  let formRequestId = null;
  let entryAnimations=[];
  let docked=false,boundProjectId=null,contextKey='standalone',dockContextVersion=0;
  const drafts=new Map();
  const contextRuns=new Map();
  root.innerHTML = `
    <div class="agent-topbar"><span class="agent-location">${icon('sparkle')}<span>Agent 创作</span></span></div>
    <div class="agent-scroll" id="agentScroll"><div class="agent-welcome"><div class="agent-eyebrow">从一个想法，到一部作品</div><h1><span class="agent-hero-mark">${icon('sparkle')}</span><span class="agent-hero-intro">和<span class="agent-brand-name">建绘通</span></span><div class="agent-modes" role="tablist" aria-label="创作类型"><button class="selected" data-mode="create" role="tab" aria-selected="true">创作</button><button data-mode="drama" role="tab" aria-selected="false">短剧</button><button data-mode="marketing" role="tab" aria-selected="false">营销</button></div><span>聊聊创作想法</span></h1><p>写下你的故事，让导演、素材与镜头一起就位。</p></div><div class="agent-messages" id="agentMessages" aria-live="polite" aria-relevant="additions"></div></div>
    <div class="agent-composer-wrap"><form class="agent-composer" id="agentComposer"><div class="agent-attached" id="agentAttached"></div><label class="sr-only" for="agentInput">故事或消息</label><textarea id="agentInput" rows="4" maxlength="24000" placeholder="输入你的故事或创作想法，输入 @ 可引用资产库里的图片、视频和音频…"></textarea><div class="agent-compose-tools"><div class="agent-tool-group agent-central-tools"><button type="button" class="agent-pill" data-command="skills">${icon('sparkle')}技能 ${icon('chevronDown')}</button></div><div class="agent-tool-group"><button type="submit" class="agent-send" aria-label="发送消息" disabled>${icon('arrowUp')}</button></div></div></form></div>
    <input type="file" id="agentUpload" multiple hidden accept="image/*,video/mp4,video/webm,audio/*">
    <dialog class="agent-dialog" id="agentDialog"><div class="agent-dialog-head"><h2></h2><button class="agent-icon" type="button" aria-label="关闭">${icon('close')}</button></div><div class="agent-dialog-body"></div></dialog>`;
  const $ = selector => root.querySelector(selector);
  const input = $('#agentInput'), send = $('.agent-send'), dialog = $('#agentDialog');
  const quickPreferences=createHomePreferences({root,icon,escapeHtml:esc});
  const stylePicker=createVisualStylePicker({api,escapeHtml:esc,icon,onError:toast});
  const quick=document.createElement('div');quick.className='home-agent-parameters';
  quick.innerHTML='<select aria-label="Agent 画幅"><option>16:9</option><option>9:16</option><option>1:1</option></select><select aria-label="Agent 目标时长"><option value="">自动时长</option><option value="15">15s</option><option value="30">30s</option><option value="60">60s</option><option value="120">120s</option></select>';
  $('.agent-central-tools').prepend(quick);
  const [quickRatio,quickDuration]=quick.querySelectorAll('select');
  quickPreferences.button(quick,{label:()=>`${quickRatio.value} · ${quickDuration.selectedOptions[0].textContent}`,groups:[{select:quickRatio,title:'选择比例',ratios:true},{select:quickDuration,title:'目标时长'}],glyph:'imageNode'});
  const styleTrigger=stylePicker.button($('.agent-central-tools'),{getValue:()=>prefs.visual_style,onChange:async value=>{
    styleTrigger.disabled=true;
    try{if(run){await applySnapshot(await api(`/api/agent/runs/${run.id}/messages`,json({request_id:crypto.randomUUID(),action:'preferences',values:{visual_style:value}})));poll();}
      else{prefs.visual_style=value;prefs.style='';formRequestId=null;}}
    finally{styleTrigger.disabled=false;}
  }});
  function syncQuickPreferences(){quickRatio.value=prefs.aspect_ratio||'16:9';const value=String(prefs.target_duration||'');if(!Array.from(quickDuration.options).some(o=>o.value===value))quickDuration.add(new Option(value+'s',value));quickDuration.value=value;quick.querySelector('.home-preference-trigger span').textContent=`${quickRatio.value} · ${quickDuration.selectedOptions[0].textContent}`;}
  quick.addEventListener('change',async()=>{
    const next={aspect_ratio:quickRatio.value,target_duration:Number(quickDuration.value)||null};quickPreferences.close();stylePicker.close();quick.querySelector('button').disabled=true;
    try{if(run){await applySnapshot(await api(`/api/agent/runs/${run.id}/messages`,json({request_id:crypto.randomUUID(),action:'preferences',values:next})));poll();}else Object.assign(prefs,next);}
    catch(error){toast(error.message);}finally{syncQuickPreferences();quick.querySelector('button').disabled=false;}
  });
  const mentions=attachAssetMentions({root,selector:'#agentInput',icon,escapeHtml:esc,onError:message=>toast(message),
    getReferences:()=>files.map((asset,index)=>({token:'@'+asset.name,assetId:asset.id,name:asset.name,kind:asset.kind,url:asset.url,number:index+1})),
    getItems:async()=>{
      const [assets,characters]=await Promise.all([api('/api/assets'),api('/api/characters')]);
      return [...assets.map(asset=>({assetId:asset.id,name:asset.name,kind:asset.kind==='prop'?'image':asset.kind||'image',url:asset.url,asset})),...characters.map(character=>{
        const asset=assets.find(item=>item.id===character.asset_id);
        return asset?{assetId:asset.id,name:character.name,kind:'character',url:asset.url,asset}:null;
      }).filter(Boolean)];
    },
    onSelect:async item=>{
      await validateReferenceVideo(item.asset);
      if(files.length>=20&&!files.some(asset=>asset.id===item.assetId))throw new Error('最多引用 20 项素材');
      return ()=>{if(!files.some(asset=>asset.id===item.assetId))files.push(item.asset);renderAttached();};
    }
  });
  let followEnd=true;
  const scrollArea=$('#agentScroll');
  scrollArea.addEventListener('wheel',e=>{if(e.deltaY<0)followEnd=false;},{passive:true});
  scrollArea.addEventListener('touchstart',()=>{followEnd=false;},{passive:true});
  scrollArea.addEventListener('pointerdown',e=>{if(e.target===scrollArea)followEnd=false;});
  scrollArea.addEventListener('scroll',()=>{if(scrollArea.scrollHeight-scrollArea.scrollTop-scrollArea.clientHeight<50)followEnd=true;},{passive:true});
  new ResizeObserver(()=>{if(followEnd&&visible&&run)scrollArea.scrollTop=scrollArea.scrollHeight;}).observe($('#agentMessages'));
  dialog.querySelector('.agent-dialog-head button').onclick = () => dialog.close();
  dialog.addEventListener('click', e => {if(e.target === dialog) dialog.close();});

  function modal(title, html) {
    dialog.querySelector('h2').textContent=title;
    dialog.querySelector('.agent-dialog-body').innerHTML=html;
    if(!dialog.open) dialog.showModal();
    return dialog.querySelector('.agent-dialog-body');
  }
  function modelList(kind) {return (capabilities?.models?.models || []).filter(m=>m.kind===kind);}
  function refreshInput() {
    send.disabled = busy || !input.value.trim();
    input.rows = run ? 2 : 4;
    input.style.height='0px';input.style.height=Math.min(run?140:220,Math.max(run?60:130,input.scrollHeight))+'px';
  }
  function setRun(value) {
    run=value;root.classList.toggle('has-conversation',!!run);root.classList.toggle('has-agent-conversation',!!run&&!docked);
    if(run&&!docked)root.classList.remove('home-landing');
    root.dataset.conversationId=run?.creation_type==='conversation'?run.project_id:'';
    root.dataset.status=run?.status||'idle';
    const heading=run ? `${run.title} · ${STATUS[run.status] || run.status}`:'新对话';root.dataset.agentTitle=heading;
    if(docked||!root.dataset.homeMode||root.dataset.homeMode==='agent')$('.agent-location span').textContent=heading;
    if(run) {Object.assign(prefs,{visual_style:null},run.preferences);prefs.canvas=false;syncQuickPreferences();stylePicker.refresh();}
    input.placeholder=run?'继续描述修改要求，或询问当前进度…':'输入你的故事或创作想法，输入 @ 可引用资产库里的图片、视频和音频…';
    refreshInput();
    renderControls();
  }
  function renderControls() {
    root.querySelector('.agent-run-controls')?.remove();
    if(!run)return;
    const controls=document.createElement('div'); controls.className='agent-run-controls';
    const active=['running','waiting_tool'].includes(run.status);
    controls.innerHTML=`<span><i class="agent-status-dot ${active?'working':''}"></i>${esc(PHASES[run.phase]||run.phase)}</span>${active?'<button data-action="pause">暂停</button>':''}${['paused','blocked'].includes(run.status)?'<button data-action="resume">继续查询</button>':''}${run.status==='blocked'?`<button data-action="retry" title="${esc(run.retry_warning||'仅重新提交失败或未通过检查的步骤')}">${run.retry_warning?'重新请求（可能再次计费）':'重试失败步骤'}</button><button data-command="preferences">更换模型</button>`:''}${!['completed','cancelled'].includes(run.status)?'<button data-action="cancel">取消</button>':''}`;
    // Recovery belongs beside the latest error, where the user is reading.
    // The normal toolbar stays above the feed while production is active.
    if(run.status==='blocked'){
      controls.classList.add('agent-recovery-controls');
      controls.setAttribute('aria-label','处理创作失败');
      $('#agentMessages').after(controls);
    }else $('#agentMessages').before(controls);
  }
  function timeLabel(t){return new Date(t*1000).toLocaleString('zh-CN',{hour12:false});}
  function renderEvent(e) {
    if(e.kind==='issue')e={...e,text:normalizeContentError(e.text)};
    if(e.kind==='task') {
      let item=root.querySelector(`[data-task-id="${CSS.escape(e.task_id)}"]`);
      if(!item){item=document.createElement('div');item.className='agent-message agent-task';item.dataset.taskId=e.task_id;$('#agentMessages').append(item);}
      const done=['done','succeeded'].includes(e.status), failed=['failed','blocked','cancelled'].includes(e.status);
      item.innerHTML=`<div class="agent-task-note">${done?icon('check'):failed?icon('warning'):AGENT_LOADER}${esc(done?'生成任务已完成':failed?'生成任务需要处理':e.text)}${!done&&!failed?'<small>关闭页面后可从创作记录继续</small>':''}</div>`;return;
    }
    if(e.kind==='step') {
      let item=root.querySelector(`[data-step-id="${CSS.escape(e.step_id)}"]`);
      if(!item){item=document.createElement('details');item.className='agent-step';item.dataset.stepId=e.step_id;$('#agentMessages').append(item);}
      item.innerHTML=`<summary><span class="agent-step-mark ${e.status}">${e.status==='done'?icon('check'):e.status==='failed'?icon('warning'):AGENT_LOADER}</span>${esc(e.text)}<span class="agent-step-state">${{done:'已完成',failed:'需要处理',running:'处理中'}[e.status]}</span></summary><div>记录时间 ${timeLabel(e.time)}</div>`;return;
    }
    if(e.kind==='review_resolved') {
      const card=root.querySelector(`[data-review="${CSS.escape(e.review_id)}"]`);
      if(card){card.classList.add('submitted');card.querySelector('.agent-review-status').textContent='已提交';card.querySelectorAll('button,input').forEach(el=>el.disabled=true);const bar=card.querySelector('.agent-review-actions');if(bar)bar.hidden=true;}return;
    }
    const el=document.createElement('div');el.dataset.event=e.seq;
    el.className=`agent-message ${e.kind==='artifact'?'agent-artifact-message':'agent-'+e.kind}`;
    if(e.kind==='user') {el.innerHTML=`<div class="agent-user-bubble">${esc(e.text)}</div>`;}
    else if(e.kind==='phase'){el.innerHTML=`<div class="agent-phase-line"><span></span>${esc(e.text)}<span></span></div>`;}
    else if(e.kind==='review'){renderReview(el,e.review);}
    else if(e.kind==='artifact'){el.innerHTML=`<button class="agent-artifact" data-artifact="${esc(e.artifact.id||e.artifact.asset_id)}"><span class="agent-file-icon">${icon('text')}</span><span><b>${esc(e.artifact.title)}</b><small>文档 · 版本 ${e.artifact.revision}</small></span><span>查看文件 ${icon('chevronRight')}</span></button>`;}
    else if(e.kind==='media'||e.kind==='delivery'){
      const a=e.asset;const url=safeURL(a.url);
      const mediaKind=a.kind==='image'||a.kind==='prop'?'image':a.kind==='audio'?'audio':'video';
      el.innerHTML=`<div class="agent-media-card agent-media-${mediaKind}"><div>${mediaKind==='image'?`<button type="button" class="agent-image-preview" aria-label="放大预览 ${esc(a.name)}"><img src="${url}" alt="${esc(a.name)}" loading="lazy"></button>`:mediaKind==='audio'?`<audio src="${url}" controls preload="metadata"></audio>`:`<video data-conversation-video src="${url}" controls preload="metadata" playsinline></video>`}</div><footer><span>${esc(e.text)}</span><button type="button" class="agent-save-library" data-player-library data-library-saved="${!!a.in_library}" aria-busy="false" ${a.in_library?'disabled':''}>${icon('library')}${a.in_library?'已保存至资产库':'保存至资产库'}</button><a href="${url}" download="${esc(a.name)}">下载 ${icon('arrowUpRight')}</a></footer></div>`;
      el.querySelector('.agent-save-library').libraryAsset=a;
      el.querySelector('.agent-save-library').onclick=async event=>{
        const button=event.currentTarget;if(button.disabled||a.in_library)return;
        button.disabled=true;button.setAttribute('aria-busy','true');button.innerHTML=icon('library')+'正在保存…';
        try{const saved=await onSaveAsset?.(a,true);if(saved)Object.assign(a,saved);}
        finally{button.disabled=!!a.in_library;button.dataset.librarySaved=String(!!a.in_library);button.setAttribute('aria-busy','false');button.innerHTML=icon('library')+(a.in_library?'已保存至资产库':'保存至资产库');}
      };
      if(mediaKind==='image')el.querySelector('.agent-image-preview').onclick=()=>onPreviewAsset?.(a);
      const media=el.querySelector('img,video');
      if(media){
        const fitCard=()=>{
          const width=media.naturalWidth||media.videoWidth,height=media.naturalHeight||media.videoHeight;
          // Size the whole card to the source ratio; constrain tall media by narrowing
          // the card instead of introducing a letterbox or cropping the picture.
          if(width>0&&height>0)media.closest('.agent-media-card').style.setProperty('--agent-media-width',`${Math.min(660,440*width/height)}px`);
        };
        media.addEventListener(mediaKind==='image'?'load':'loadedmetadata',fitCard,{once:true});
        fitCard();
      }
    }else if(e.kind==='assistant') {
      const prose=document.createElement('div');prose.className='agent-prose';
      prose.append(renderMarkdown(e.text));el.append(prose);
      const meta=document.createElement('div');meta.className='agent-message-meta';
      meta.innerHTML=`${icon('sparkle')} ${timeLabel(e.time)}`;el.append(meta);
    }else {el.innerHTML=`<div class="agent-prose">${esc(e.text).replace(/\n/g,'<br>')}</div>`;}
    const day=new Date((e.time||Date.now()/1000)*1000),key=day.toLocaleDateString('sv-SE');
    if(!$('#agentMessages').querySelector(`[data-agent-date="${key}"]`)){
      const heading=document.createElement('h2');heading.className='home-result-date';heading.dataset.agentDate=key;heading.textContent=key===new Date().toLocaleDateString('sv-SE')?'今天':day.toLocaleDateString('zh-CN');$('#agentMessages').append(heading);
    }
    $('#agentMessages').append(el);
  }
  function safeURL(url){return typeof url==='string'&&url.startsWith('/uploads/')&&!url.startsWith('//')?esc(url):'';}
  function renderReview(el,review) {
    el.className='agent-review-card';el.dataset.review=review.id;
    let page=0;const answers={};const questions=review.questions||[];
    questions.forEach(q=>{answers[q.key]=prefs[q.key]??q.options[0]?.value;});
    function draw(){
      const q=questions[page];
      el.innerHTML=`<div class="agent-review-head"><strong>${esc(q?.title||review.title)}</strong><div><span class="agent-review-status">待确认</span>${questions.length?`<button class="agent-pager" data-page="prev" aria-label="上一项" ${page===0?'disabled':''}>${icon('chevronLeft')}</button><span>${page+1}/${questions.length}</span><button class="agent-pager" data-page="next" aria-label="下一项" ${page===questions.length-1?'disabled':''}>${icon('chevronRight')}</button>`:''}</div></div>${q?`<div class="agent-review-options" role="radiogroup" aria-label="${esc(q.title)}">${q.options.map((o,i)=>`<label class="agent-choice ${answers[q.key]===o.value?'selected':''}"><input type="radio" name="${review.id}-${q.key}" value="${esc(o.value)}" ${answers[q.key]===o.value?'checked':''}><span class="agent-option-number">${i+1}</span><span><b>${esc(o.label)}</b>${o.description?`<small>${esc(o.description)}</small>`:''}</span></label>`).join('')}</div>`:''}<p class="agent-review-summary">${esc(review.summary||'')}</p><div class="agent-review-actions">${questions.length&&page<questions.length-1?'<button class="agent-confirm" data-page="next">下一项</button>':review.choices.map(c=>`<button class="agent-confirm" data-review-action="${esc(c.id)}">${esc(c.label)}</button>`).join('')}</div>`;
      el.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{page+=b.dataset.page==='next'?1:-1;draw();});
      el.querySelectorAll('input[type="radio"]').forEach(r=>r.onchange=()=>{
        answers[q.key]=r.value;
        // Keep the focused radio in place so selecting an option (including
        // with arrow keys) cannot move focus or scroll the outer chat panel.
        el.querySelectorAll('.agent-choice').forEach(choice=>choice.classList.toggle('selected',choice.querySelector('input').checked));
      });
      el.querySelectorAll('[data-review-action]').forEach(b=>b.onclick=()=>act(b.dataset.reviewAction,{review_id:review.id,values:answers},b));
    }
    draw();
  }
  async function applySnapshot(data,full=false) {
    const scroller=$('#agentScroll');const bottom=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<130;
    if(full){$('#agentMessages').innerHTML='';cursor=0;followEnd=true;}
    setRun(data.run);
    (data.events||[]).filter(e=>e.seq>cursor).forEach(renderEvent);
    if(full&&!docked){
      entryAnimations.forEach(animation=>animation.cancel());entryAnimations=[];
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches)entryAnimations=[...root.querySelectorAll(':scope > .agent-topbar,:scope > .agent-composer-wrap')].map(el=>el.animate([{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],{duration:300,easing:'ease',fill:'both'}));
    }
    cursor=Math.max(cursor,...(data.events||[]).map(e=>e.seq));
    // Old pending cards restored after a refresh may have been superseded by a revision.
    root.querySelectorAll('[data-review]').forEach(card=>{if(card.dataset.review!==run.pending_review?.id&&!card.classList.contains('submitted')){card.classList.add('submitted');card.querySelector('.agent-review-status').textContent='已结束';card.querySelectorAll('button,input').forEach(b=>b.disabled=true);}});
    if(full||followEnd||bottom)requestAnimationFrame(()=>scroller.scrollTo({top:scroller.scrollHeight,behavior:'instant'}));
    localStorage.setItem(userStorageKey('jht.agent.lastRun'),run.id);
    if(docked&&run.project_id===boundProjectId)await onProjectSnapshot?.(run.project_id,data.project_revision);
    if(run.creation_type==='conversation')onConversationSnapshot?.(run);
  }
  async function poll(){
    clearTimeout(polling);
    if(!visible||!run)return;
    const id=run.id;
    try{const data=await api(`/api/agent/runs/${encodeURIComponent(id)}?after=${cursor}`);if(run?.id!==id||!visible)return;await applySnapshot(data);}catch(error){if(!document.hidden)toast(error.message);}
    if(run?.id===id&&visible)polling=setTimeout(poll,['running','waiting_tool'].includes(run.status)?1500:4500);
  }
  async function act(action,extra={},button){
    if(!run)return;if(button)button.disabled=true;
    followEnd=true;
    try{await applySnapshot(await api(`/api/agent/runs/${run.id}/messages`,json({request_id:crypto.randomUUID(),action,...extra})));poll();}
    catch(error){toast(error.message);if(button)button.disabled=false;}
  }
  async function submit(e){
    e.preventDefault();const text=input.value.trim();if(!text||busy)return;
    busy=true;refreshInput();
    followEnd=true;
    try{
      formRequestId ||= crypto.randomUUID();
      let data;
      if(run){data=await api(`/api/agent/runs/${run.id}/messages`,json({text,request_id:formRequestId,asset_ids:files.map(a=>a.id)}));}
      else{await beforeStart?.();data=await api('/api/agent/runs',json({text,request_id:formRequestId,asset_ids:files.map(a=>a.id),...(docked&&boundProjectId?{project_id:boundProjectId}:{}),preferences:{...prefs,canvas:false,scope:'full_video',mode}}));}
      input.value='';formRequestId=null;files=[];renderAttached();await applySnapshot(data);poll();
    }catch(error){toast(error.message);}finally{busy=false;refreshInput();}
  }
  function renderAttached(){
    $('#agentAttached').innerHTML=files.map((a,index)=>`<span class="agent-reference" title="${esc(a.name)}" ${a.kind==='audio'?`data-audio-preview="${esc(a.url)}"`:''}>${a.kind==='video'?videoReferenceCover(a,esc):['image','prop'].includes(a.kind)?`<img src="${esc(a.url)}" alt="${esc(a.name)}">`:a.kind==='audio'?'<span class="reference-audio-icon">♪</span>':icon('video')}<span class="reference-number">${index+1}</span>${a.kind==='audio'&&a.duration?`<span class="reference-duration">${Number(a.duration).toFixed(1)}s</span>`:''}<button type="button" data-remove="${esc(a.id)}" aria-label="移除 ${esc(a.name)}">×</button></span>`).join('');
    mentions.refresh();
  }
  async function assetPicker(){
    const assets=await api('/api/assets');
    const pane=modal('引用素材',`<input class="agent-search" placeholder="搜索素材名称" aria-label="搜索素材"><div class="agent-picker-grid"></div>`);
    const draw=()=>{const term=pane.querySelector('input').value.toLowerCase();pane.querySelector('.agent-picker-grid').innerHTML=assets.filter(a=>['image','prop','audio','video'].includes(a.kind)&&a.name.toLowerCase().includes(term)).map(a=>`<button data-pick="${esc(a.id)}">${a.kind==='image'||a.kind==='prop'?`<img src="${safeURL(a.url)}" alt="">`:a.kind==='audio'?'<span class="reference-audio-icon">♪</span>':icon('video')}<span>${esc(a.name)}</span><small>${files.some(f=>f.id===a.id)?'已引用':'引用素材'}</small></button>`).join('')||'<p class="agent-empty">暂无匹配素材，可以通过 + 上传。</p>';};
    pane.querySelector('input').oninput=draw;draw();pane.onclick=e=>{const b=e.target.closest('[data-pick]');if(!b)return;const a=assets.find(a=>a.id===b.dataset.pick);if(files.length>=20)return toast('最多引用20项素材');if(!files.some(f=>f.id===a.id))files.push(a);renderAttached();dialog.close();input.focus();};
  }
  async function history(){
    const runs=(await api('/api/agent/runs')).filter(item=>docked?item.creation_type==='canvas'&&item.project_id===boundProjectId:item.creation_type==='conversation');const pane=modal('创作记录',runs.length?`<div class="agent-history">${runs.map(r=>`<button data-run="${esc(r.id)}"><span><b>${esc(r.title)}</b><small>${timeLabel(r.updated_at)}</small></span><em>${esc(STATUS[r.status]||r.status)}</em>${icon('chevronRight')}</button>`).join('')}</div>`:'<p class="agent-empty">还没有创作记录，从一个故事开始吧。</p>');
    pane.onclick=async e=>{const b=e.target.closest('[data-run]');if(!b)return;try{dialog.close();await withLoading(async()=>applySnapshot(await api(`/api/agent/runs/${b.dataset.run}`),true),"正在读取创作记录…");poll();}catch(error){toast(error.message);}};
  }
  async function preferences(){
    capabilities=await api('/api/agent/capabilities');
    const pane=modal('创作偏好',`<div class="agent-preferences"><p>选择已有模型配置。模型密钥通过「模型管理」设置。</p>${['text','image','video','audio'].map((kind,i)=>`<label>${['导演与文本模型','图片生成模型','视频生成模型','音色、配音与 BGM'][i]}<select data-model="${kind}"><option value="">使用默认模型</option>${modelList(kind).map(m=>`<option value="${esc(m.id)}" ${prefs.models[kind]===m.id?'selected':''}>${esc(m.name)}${m.api_key_set?'':' · 未配置密钥'}</option>`).join('')}</select></label>`).join('')}<label>目标时长（秒，可留空自动分析）<input id="agentTargetDuration" type="number" min="8" max="600" value="${prefs.target_duration||''}"></label><div class="agent-preference-checks">${[['voice','制作正式配音'],['music','制作整片 BGM'],['subtitles','添加台词字幕']].map(([k,t])=>`<label><input type="checkbox" data-pref="${k}" ${prefs[k]?'checked':''}>${t}</label>`).join('')}</div><div class="agent-dialog-actions"><button class="agent-quiet" data-configure-models>模型管理</button><button class="agent-confirm" data-save-preferences>保存偏好</button></div></div>`);
    pane.querySelector('[data-configure-models]').onclick=()=>{dialog.close();onModels();};
    pane.querySelector('[data-save-preferences]').onclick=async()=>{
      const next={models:{}};pane.querySelectorAll('[data-model]').forEach(s=>next.models[s.dataset.model]=s.value);pane.querySelectorAll('[data-pref]').forEach(c=>next[c.dataset.pref]=c.checked);
      const duration=Number(pane.querySelector('#agentTargetDuration').value);if(duration&&(!Number.isInteger(duration)||duration<8||duration>600))return toast('目标时长需为 8 至 600 的整数秒');next.target_duration=duration||null;
      const button=pane.querySelector('[data-save-preferences]');button.disabled=true;
      try{if(run){await applySnapshot(await api(`/api/agent/runs/${run.id}/messages`,json({request_id:crypto.randomUUID(),action:'preferences',values:next})));poll();}else Object.assign(prefs,next);
        dialog.close();syncQuickPreferences();
      }catch(error){toast(error.message);}finally{button.disabled=false;}
    };
  }
  const commands={
    new:async()=>{if(busy)return;clearTimeout(polling);run=null;cursor=0;files=[];input.value='';formRequestId=null;prefs.canvas=false;contextRuns.delete(contextKey);localStorage.removeItem(userStorageKey('jht.agent.lastRun'));$('#agentMessages').innerHTML='';root.classList.remove('with-canvas');setRun(null);renderAttached();input.focus();},
    history,assets:()=>mentions.open(input),upload:()=>$('#agentUpload').click(),preferences,
    models:()=>onModels(),
    skills:async()=>{capabilities ||= await api('/api/agent/capabilities');modal('创作技能',`<p class="agent-dialog-intro">导演会按制作阶段调用相应技能。以下能力已经接入本次创作流程。</p><div class="agent-skills">${capabilities.skills.filter(s=>s.id!=='canvas-management').map(s=>`<div>${icon('check')}<span><b>${esc(TITLES[s.id]||s.id)}</b><small>${esc(s.description)}</small></span></div>`).join('')}</div>`);},
  };
  root.addEventListener('click',async e=>{
    const c=e.target.closest('[data-command]'),action=e.target.closest('[data-action]'),remove=e.target.closest('[data-remove]'),artifact=e.target.closest('[data-artifact]'),tab=e.target.closest('[data-mode]');
    try{
      if(c){e.preventDefault();await commands[c.dataset.command]?.();}
      if(action)await act(action.dataset.action,{},action);
      if(remove){mentions.removeReference(remove.dataset.remove,input);files=files.filter(a=>a.id!==remove.dataset.remove);renderAttached();}
      if(tab){mode=tab.dataset.mode;root.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b===tab);b.setAttribute('aria-selected',String(b===tab));});}
      if(artifact){const a=await api(`/api/agent/runs/${run.id}/artifacts/${encodeURIComponent(artifact.dataset.artifact)}`);const pane=modal(a.title,`<article class="agent-document"></article><button class="agent-confirm" data-download-doc>下载文档</button>`);pane.querySelector('.agent-document').append(renderMarkdown(a.content));pane.querySelector('[data-download-doc]').onclick=()=>{const url=URL.createObjectURL(new Blob([a.content],{type:'text/markdown;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=a.title+'.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};}
    }catch(error){toast(error.message);}
  });
  $('#agentComposer').onsubmit=submit;input.oninput=()=>{formRequestId=null;refreshInput();};
  input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#agentComposer').requestSubmit();}};
  $('#agentUpload').onchange=async e=>{try{for(const file of Array.from(e.target.files).slice(0,20-files.length)){await validateReferenceVideo(file);const data=new FormData();data.append('file',file);if(run)data.append('project_id',run.project_id);files.push(await api('/api/assets',{method:'POST',body:data}));}renderAttached();}catch(error){toast(error.message);}finally{renderAttached();e.target.value='';}};
  return {
    async setDocked(value,projectId=null,skipRestore=false){
      const ticket=++dockContextVersion;
      if(value){if(ticket!==dockContextVersion)return;}
      docked=value;boundProjectId=value?projectId:null;root.classList.toggle('agent-docked',value);
      const target=value?projectId:'standalone';
      if(contextKey!==target){
        drafts.set(contextKey,{text:input.value,files:[...files]});if(run)contextRuns.set(contextKey,run.id);
        contextKey=target;clearTimeout(polling);mentions.close();run=null;cursor=0;files=[];formRequestId=null;prefs.canvas=false;
        $('#agentMessages').innerHTML='';setRun(null);
        const draft=drafts.get(target);input.value=draft?.text||'';files=draft?.files||[];renderAttached();initialized=true;
        let id=contextRuns.get(target);
        if(value&&!id){const runs=(await api('/api/agent/runs')).filter(item=>item.creation_type==='canvas'&&item.project_id===projectId).sort((a,b)=>(b.updated_at||0)-(a.updated_at||0));id=runs[0]?.id;}
        if(ticket!==dockContextVersion)return;
        if(id&&!skipRestore){const data=await api(`/api/agent/runs/${encodeURIComponent(id)}`);if(ticket!==dockContextVersion)return;await applySnapshot(data,true);}
        if(visible&&run)poll();
      }
      setRun(run);refreshInput();
    },
    fillDraft(text){input.value=String(text);input.dispatchEvent(new Event('input',{bubbles:true}));input.focus({preventScroll:true});input.setSelectionRange(input.value.length,input.value.length);},
    focus(){input.focus({preventScroll:true});},
    async newConversation(preserveDraft=false){initialized=true;++dockContextVersion;const saved={text:input.value,files:[...files]};await commands.new();if(preserveDraft){input.value=saved.text;files=saved.files;renderAttached();refreshInput();}},
    async openRun(id,snapshot=null){const data=snapshot||await api(`/api/agent/runs/${encodeURIComponent(id)}`);if(data.run.creation_type!=='conversation')throw new Error('这是画布内的对话，请从画布打开');initialized=true;await this.setDocked(false,null,true);await applySnapshot(data,true);input.value='';files=[];renderAttached();poll();},
    async show(){visible=true;try{capabilities ||= await api('/api/agent/capabilities');if(!initialized){initialized=true;const last=localStorage.getItem(userStorageKey('jht.agent.lastRun'));if(last){try{const data=await api(`/api/agent/runs/${encodeURIComponent(last)}`);if(data.run.creation_type==='conversation')await applySnapshot(data,true);}catch{localStorage.removeItem(userStorageKey('jht.agent.lastRun'));}}}if(run)poll();else refreshInput();}catch(error){toast(error.message);}},
    hide(){visible=false;clearTimeout(polling);quickPreferences.close();stylePicker.close();mentions.close();if(dialog.open)dialog.close();},
  };
}
