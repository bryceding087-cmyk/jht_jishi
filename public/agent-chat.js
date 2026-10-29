import { validateReferenceVideo, videoReferenceCover } from './reference-video.js?v=1';
import { userStorageKey } from './session.js?v=market-sdk-3';
import { icon } from './iconography.js?v=library-toggle-1';
import { renderMarkdown } from './markdown.js?v=agent-docs-1';
import { attachAssetMentions } from './asset-mentions.js?v=studio-references-2';
import { createImageLightbox } from './image-lightbox.js?v=1';
import { createHomePreferences } from './home-preferences.js?v=selection-motion-10';
import { createVisualStylePicker } from './visual-style-picker.js?v=4';
import { normalizeContentError } from './task-errors.js?v=content-rejection-3';
import { withLoading } from './loading-state.js?v=1';
import { createAgentStudio } from './agent-studio.js?v=floating-switch-1';
import { createRequestButton } from './request-button.js?v=1';

const PHASES = {assistant:'对话',studio_write:'剧本与画布创作',studio_media:'素材与成片制作',studio_ready:'本轮已完成',story_review:'导演检查',analysis:'时长分析',creative:'创意设计',assets:'参考素材',storyboard:'分镜规划',music:'声音与配乐',video:'视频生成',postproduction:'成片合成',review:'成片检查',delivery:'交付完成'};
const STATUS = {running:'正在制作',waiting_tool:'正在生成',waiting_user:'等待确认',paused:'已暂停',blocked:'需要处理',completed:'已完成',cancelled:'已取消'};
const AGENT_LOADER = `<span class="agent-loading" aria-hidden="true"><img src="${new URL('./agent-breathing-ring.svg',import.meta.url).href}" width="36" height="36" alt=""></span>`;
const TITLES = {'project-context':'创作上下文',director:'导演','story-polishing':'故事润色','model-budget':'模型与费用','story-analysis':'内容与时长','creative-design':'创意设计','visual-assets':'视觉素材','storyboard-planning':'分镜规划','prompt-composition':'提示词','video-generation':'视频生成','voice-production':'音色与配音','music-production':'整片配乐','video-editing':'视频合成','quality-review':'质量检查','canvas-management':'画布管理','revision-recovery':'修改与恢复','studio-writing':'剧本创作','studio-assets':'资产与分镜拆解','studio-production':'画布与成片制作'};

export function createAgentChat({api, json, escapeHtml: esc, toast, onModels, beforeStart, getCanvasSelection, onCanvasActions, onProjectSnapshot, onConversationSnapshot, onPreviewAsset, onSaveAsset, onOpenCanvas}) {
  const root = document.getElementById('agentView');
  const referencePreview = createImageLightbox({onError:toast});
  document.addEventListener('asset-library-change',({detail:asset})=>{
    root.querySelectorAll('.agent-save-library').forEach(button=>{
      if(button.libraryAsset?.id!==asset.id)return;
      Object.assign(button.libraryAsset,asset);
      button.dataset.librarySaved=String(!!asset.in_library);
      button.disabled=!!asset.in_library||button.getAttribute('aria-busy')==='true';
      button.innerHTML=icon('library')+(asset.in_library?'已保存至资产库':'保存至资产库');
    });
  });
  let run = null, capabilities = null, cursor = 0, polling = null, visible = false, files = [], mode = 'create', initialized = false;
  const prefs = {aspect_ratio:'16:9',style:'',visual_style:null,scope:'full_video',voice:true,music:true,subtitles:true,canvas:false,models:{}};
  let formRequestId = null;
  let entryAnimations=[];
  let docked=false,boundProjectId=null,contextKey='standalone',dockContextVersion=0;
  let pendingSubmission=null,draftRevision=0,workerStopping=false;
  const stopRequests=new Map();
  const drafts=new Map();
  const contextRuns=new Map();
  const appliedCanvasCommands=new Set();
  root.innerHTML = `
    <div class="agent-topbar"><span class="agent-location">${icon('sparkle')}<span>Agent 创作</span></span></div>
    <div class="agent-scroll" id="agentScroll"><div class="agent-welcome"><div class="agent-eyebrow">从一个想法，到一部作品</div><h1><span class="agent-hero-mark">${icon('sparkle')}</span><span class="agent-hero-intro">和<span class="agent-brand-name">建绘通</span></span><div class="agent-modes" role="tablist" aria-label="创作类型"><button class="selected" data-mode="create" role="tab" aria-selected="true">创作</button><button data-mode="drama" role="tab" aria-selected="false">短剧</button><button data-mode="marketing" role="tab" aria-selected="false">营销</button></div><span>聊聊创作想法</span></h1><p>写下你的故事，让导演、素材与镜头一起就位。</p></div><div class="agent-messages" id="agentMessages" aria-live="polite" aria-relevant="additions"></div></div>
    <div class="agent-composer-wrap"><form class="agent-composer" id="agentComposer"><div class="agent-attached" id="agentAttached"></div><label class="sr-only" for="agentInput">故事或消息</label><textarea id="agentInput" rows="4" maxlength="24000" placeholder="输入你的故事或创作想法，输入 @ 可引用资产库里的图片、视频和音频…"></textarea><div class="agent-compose-tools"><div class="agent-tool-group agent-central-tools"><button type="button" class="agent-pill" data-command="skills">${icon('sparkle')}技能 ${icon('chevronDown')}</button></div><div class="agent-tool-group"><button type="submit" class="agent-send" aria-label="发送消息" disabled>${icon('arrowUp')}</button></div></div></form></div>
    <input type="file" id="agentUpload" multiple hidden accept="image/*,video/mp4,video/webm,audio/*">
    <dialog class="agent-dialog" id="agentDialog"><div class="agent-dialog-head"><h2></h2><button class="agent-icon" type="button" aria-label="关闭">${icon('close')}</button></div><div class="agent-dialog-body"></div></dialog>`;
  const $ = selector => root.querySelector(selector);
  const input = $('#agentInput'), send = $('.agent-send'), dialog = $('#agentDialog');
  const sendControl=createRequestButton(send,{onStop:stopCurrent,onError:error=>{toast(error.message);refreshInput();}});
  const studio=createAgentStudio({root,api,json,esc,icon,toast,onSnapshot:applySnapshot,onAction:act,onOpenCanvas});
  const intentSelect=document.createElement('select');intentSelect.className='agent-intent-select';intentSelect.setAttribute('aria-label','Agent 创作任务');
  intentSelect.innerHTML=[['auto','自动识别'],['image','生成图片'],['video','生成视频'],['script','剧本创作'],['produce','一句话短剧']].map(([value,label])=>`<option value="${value}">${label}</option>`).join('');
  const quickPreferences=createHomePreferences({root,icon,escapeHtml:esc});
  const intent=document.createElement('div');intent.className='home-agent-parameters agent-intent-control';intent.append(intentSelect);
  $('.agent-central-tools').prepend(intent);
  quickPreferences.button(intent,{label:()=>intentSelect.selectedOptions[0].textContent,groups:[{select:intentSelect,title:'创作任务',menu:true}],glyph:'sparkle'});
  intentSelect.addEventListener('change',()=>{formRequestId=null;});
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
    const next={aspect_ratio:quickRatio.value,target_duration:Number(quickDuration.value)||null};stylePicker.close();quick.querySelector('button').disabled=true;quickRatio.disabled=quickDuration.disabled=true;quickPreferences.refresh();
    try{if(run){await applySnapshot(await api(`/api/agent/runs/${run.id}/messages`,json({request_id:crypto.randomUUID(),action:'preferences',values:next})));poll();}else Object.assign(prefs,next);}
    catch(error){toast(error.message);}finally{syncQuickPreferences();quick.querySelector('button').disabled=false;quickRatio.disabled=quickDuration.disabled=false;quickPreferences.refresh();}
  });
  const mentions=attachAssetMentions({root,selector:'#agentInput',icon,escapeHtml:esc,onError:message=>toast(message),
    onPreviewImage:asset=>referencePreview.open(asset),
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
  const currentSubmission=()=>pendingSubmission?.version===dockContextVersion?pendingSubmission:null;
  const activeRun=()=>!!run&&['running','waiting_tool'].includes(run.status);
  function refreshThinking(active){
    const feed=$('#agentMessages');
    let indicator=feed.querySelector('.agent-thinking');
    if(!active){indicator?.remove();return;}
    if(!indicator){
      indicator=document.createElement('div');indicator.className='agent-step agent-thinking';
      indicator.setAttribute('role','status');
      indicator.innerHTML=`${AGENT_LOADER}<span>思考中...</span>`;
    }
    if(feed.lastElementChild!==indicator)feed.append(indicator);
  }
  function refreshInput() {
    const submission=currentSubmission();
    const stopping=submission?.stopRequested||workerStopping||[...stopRequests.values()].some(stop=>stop.version===dockContextVersion);
    refreshThinking(!stopping&&!!(submission||activeRun()));
    sendControl.setState(stopping?'stopping':submission||activeRun()?'running':'idle',{disabled:!input.value.trim()});
    input.rows = run ? 2 : 4;
    input.style.height='0px';input.style.height=Math.min(run?140:220,Math.max(run?60:130,input.scrollHeight))+'px';
  }
  function setRun(value) {
    run=value;root.classList.toggle('has-conversation',!!run);root.classList.toggle('has-agent-conversation',!!run&&!docked);
    if(run&&!docked)root.classList.remove('home-landing');
    root.dataset.conversationId=run?.creation_type==='conversation'?run.project_id:'';
    root.dataset.status=run?.status||'idle';
    const thinking=run?.phase==='assistant'&&run.status==='running';
    const heading=run ? `${run.title} · ${thinking?'思考中...':STATUS[run.status] || run.status}`:'新对话';root.dataset.agentTitle=heading;
    const shortDrama=!!run?.studio?.active;
    root.classList.toggle('has-studio-assistant',shortDrama);
    if(docked||!root.dataset.homeMode||root.dataset.homeMode==='agent')$('.agent-location').innerHTML=shortDrama?'<span class="agent-assistant-orb" aria-hidden="true"></span><span>短剧助手</span>':`${icon('sparkle')}<span>${esc(heading)}</span>`;
    if(run) {Object.assign(prefs,{visual_style:null},run.preferences);prefs.canvas=false;syncQuickPreferences();stylePicker.refresh();}
    input.placeholder=run?'继续描述修改要求，或询问当前进度…':'输入你的故事或创作想法，输入 @ 可引用资产库里的图片、视频和音频…';
    refreshInput();
    renderControls();
    studio.update(run,docked);
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
  function processTurn(){return [...$('#agentMessages').querySelectorAll(':scope > .agent-user')].at(-1)?.dataset.event||'initial';}
  function processDetails(){
    const feed=$('#agentMessages'),turn=processTurn();
    let group=feed.querySelector(`[data-process-turn="${CSS.escape(turn)}"]`);
    if(!group){
      group=document.createElement('details');group.className='agent-process-details';group.dataset.processTurn=turn;
      group.innerHTML=`<summary><span>处理详情</span><small class="agent-process-state"></small><small class="agent-process-count"></small>${icon('chevronDown')}</summary><div class="agent-process-events"></div>`;
      group.addEventListener('toggle',()=>{followEnd=false;});
      feed.append(group);
    }
    return group.querySelector('.agent-process-events');
  }
  function refreshProcessDetails(){
    for(const group of $('#agentMessages').querySelectorAll('.agent-process-details')){
      const steps=[...group.querySelectorAll('[data-step-status]')],total=steps.length;
      const done=steps.filter(step=>step.dataset.stepStatus==='done').length;
      const failed=steps.filter(step=>step.dataset.stepStatus==='failed').length;
      const current=group.dataset.processTurn===processTurn();
      const state=failed?'需要处理':current&&run?.status==='cancelled'?'已停止':current&&run?.status==='paused'?'已暂停':
        current&&activeRun()?'进行中':done===total?'已完成':'已结束';
      group.dataset.processState=state;
      group.querySelector('.agent-process-state').textContent=state;
      group.querySelector('.agent-process-count').textContent=total?`${done}/${total}`:'';
      for(const step of steps){
        if(step.dataset.stepStatus!=='running')continue;
        step.querySelector('.agent-step-state').textContent=current&&activeRun()?'进行中':state==='已暂停'?'已暂停':state==='已停止'?'已停止':'已结束';
      }
    }
  }
  function renderEvent(e) {
    if(e.kind==='canvas_command')e={...e,kind:'assistant'};
    if(e.kind==='issue')e={...e,text:normalizeContentError(e.text)};
    if(e.kind==='task') {
      let item=root.querySelector(`[data-task-id="${CSS.escape(e.task_id)}"]`);
      if(!item){item=document.createElement('div');item.className='agent-step agent-task';item.dataset.taskId=e.task_id;processDetails().append(item);}
      const done=['done','succeeded','completed'].includes(e.status), failed=['failed','blocked'].includes(e.status), stopped=['cancelled','canceled'].includes(e.status);
      item.dataset.stepStatus=done?'done':failed?'failed':stopped?'cancelled':'running';
      item.innerHTML=`<span class="agent-step-mark ${item.dataset.stepStatus}">${icon(done?'check':failed?'warning':'clock')}</span><span>${esc(done?'生成任务已完成':failed?'生成任务需要处理':stopped?'生成任务已停止':e.text||'生成任务已受理')}</span><span class="agent-step-state">${done?'已完成':failed?'需要处理':stopped?'已停止':'进行中'}</span>`;return;
    }
    if(e.kind==='step') {
      // A repaired response updates the same stage instead of adding a second row.
      const stepId=e.step_id.includes(':op:studio:')?e.step_id.replace(/:repair:(\d+)$/,':$1'):e.step_id;
      let item=root.querySelector(`[data-step-id="${CSS.escape(stepId)}"]`);
      if(!item){item=document.createElement('div');item.className='agent-step';item.dataset.stepId=stepId;processDetails().append(item);}
      item.dataset.stepStatus=e.status;
      item.innerHTML=`<span class="agent-step-mark ${e.status}">${icon(e.status==='done'?'check':e.status==='failed'?'warning':'clock')}</span><span>${esc(e.text)}</span><span class="agent-step-state">${{done:'已完成',failed:'需要处理',running:'进行中'}[e.status]||''}</span>`;return;
    }
    if(e.kind==='progress'||e.kind==='phase'){
      const note=document.createElement('div');note.className='agent-process-note';note.dataset.event=e.seq;note.textContent=e.text;
      processDetails().append(note);return;
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
    else if(e.kind==='canvas'){el.innerHTML=`<button class="studio-canvas-link" type="button">${icon('grid')}<span>${esc(e.text)}</span>${icon('arrowUpRight')}</button>`;el.querySelector('button').onclick=()=>onOpenCanvas?.(e.project_id,null,run.id);}
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
    if(!full&&run&&data.run.id!==run.id)return;
    if(!full&&run&&data.run.id===run.id&&Number(data.run.revision)<Number(run.revision))return;
    const scroller=$('#agentScroll');const bottom=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<130;
    const canvasCommands=full?[]:(data.events||[]).filter(e=>e.seq>cursor&&e.kind==='canvas_command');
    if(full){$('#agentMessages').innerHTML='';cursor=0;followEnd=true;}
    workerStopping=data.run.stopping===true||data.stopping===true;setRun(data.run);
    (data.events||[]).filter(e=>e.seq>cursor).forEach(renderEvent);
    // One indicator follows run/submission state, including restored histories
    // and runs stopped without a closing event for each in-flight task.
    refreshInput();
    refreshProcessDetails();
    if(full&&!docked){
      entryAnimations.forEach(animation=>animation.cancel());entryAnimations=[];
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches)entryAnimations=[...root.querySelectorAll(':scope > .agent-topbar,:scope > .agent-composer-wrap')].map(el=>el.animate([{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],{duration:300,easing:'ease',fill:'both'}));
    }
    cursor=Math.max(cursor,...(data.events||[]).map(e=>e.seq));
    // Old pending cards restored after a refresh may have been superseded by a revision.
    root.querySelectorAll('[data-review]').forEach(card=>{if(card.dataset.review!==run.pending_review?.id&&!card.classList.contains('submitted')){card.classList.add('submitted');card.querySelector('.agent-review-status').textContent='已结束';card.querySelectorAll('button,input').forEach(b=>b.disabled=true);}});
    if(full||followEnd||bottom)requestAnimationFrame(()=>scroller.scrollTo({top:scroller.scrollHeight,behavior:'instant'}));
    localStorage.setItem(userStorageKey('jht.agent.lastRun'),run.id);
    if(docked){
      const canvasId=run.studio?.canvas_project_id||run.project_id;
      if(canvasId===boundProjectId)await onProjectSnapshot?.(canvasId,data.canvas_project_revision??data.project_revision);
    }
    if(run.creation_type==='conversation')onConversationSnapshot?.(run);
    if(docked)for(const command of canvasCommands){
      if(command.canvas_project_id!==boundProjectId||appliedCanvasCommands.has(command.command_id))continue;
      appliedCanvasCommands.add(command.command_id);
      if(appliedCanvasCommands.size>500)appliedCanvasCommands.delete(appliedCanvasCommands.values().next().value);
      await onCanvasActions?.(boundProjectId,command.ui||[],run.id);
    }
  }
  async function poll(){
    clearTimeout(polling);
    if(!visible||!run)return;
    const id=run.id,version=dockContextVersion;
    try{const data=await api(`/api/agent/runs/${encodeURIComponent(id)}?after=${cursor}`);if(run?.id!==id||version!==dockContextVersion||!visible)return;await applySnapshot(data);}catch(error){if(version===dockContextVersion&&!document.hidden)toast(error.message);}
    if(run?.id===id&&version===dockContextVersion&&visible)polling=setTimeout(poll,activeRun()||workerStopping?1500:4500);
  }
  async function stopRun(id,version,requestId=null){
    const key=requestId||id;
    if(stopRequests.has(key))return stopRequests.get(key).promise;
    const record={version,runId:id};
    const request=Promise.resolve().then(async()=>{
      try{
        const data=await api('/api/generation/stop',json({kind:'agent',...(id?{run_id:id}:{}),...(requestId?{request_id:requestId}:{})}));
        if(version===dockContextVersion&&id&&run?.id===id){
          const snapshot=data.run?data:await api(`/api/agent/runs/${encodeURIComponent(id)}`);
          if(version===dockContextVersion&&run?.id===id){await applySnapshot(snapshot);poll();}
        }
      }finally{
        stopRequests.delete(key);if(version===dockContextVersion)refreshInput();
      }
    });
    record.promise=request;stopRequests.set(key,record);refreshInput();return request;
  }
  async function stopCurrent(){
    const submission=currentSubmission();
    if(submission){
      submission.stopRequested=true;refreshInput();
      submission.stopPromise=stopRun(submission.runId,submission.version,submission.requestId).catch(error=>{submission.stopRequested=false;if(submission.version===dockContextVersion){toast(error.message);refreshInput();}});
      await submission.stopPromise;
      return;
    }
    if(!activeRun())return refreshInput();
    const id=run.id,version=dockContextVersion;
    try{await stopRun(id,version);}catch(error){if(version===dockContextVersion){toast(error.message);refreshInput();}}
  }
  async function act(action,extra={},button){
    if(!run||currentSubmission())return;if(button)button.disabled=true;
    const version=dockContextVersion,id=run.id;
    followEnd=true;
    const submission=['cancel','pause'].includes(action)?null:{version,stopRequested:false,requestId:crypto.randomUUID(),runId:id};
    if(submission){pendingSubmission=submission;refreshInput();}
    try{
      if(action==='cancel'){await stopRun(id,version);return;}
      const data=await api(`/api/agent/runs/${id}/messages`,json({request_id:submission?.requestId||crypto.randomUUID(),action,...extra}));
      if(version!==dockContextVersion||run?.id!==id)return;
      await applySnapshot(data);poll();
      if(submission?.stopRequested){
        await submission.stopPromise;
        const snapshot=await api(`/api/agent/runs/${encodeURIComponent(id)}`);
        if(version===dockContextVersion&&run?.id===id)await applySnapshot(snapshot);
      }
    }
    catch(error){if(version===dockContextVersion){if(error.detail?.code!=='generation_request_stopped')toast(error.message);if(button)button.disabled=false;}}
    finally{if(submission&&pendingSubmission===submission)pendingSubmission=null;if(version===dockContextVersion)refreshInput();}
  }
  async function submit(e){
    e.preventDefault();const text=input.value.trim();if(!text||currentSubmission()||activeRun()||workerStopping||[...stopRequests.values()].some(stop=>stop.version===dockContextVersion))return;
    const version=dockContextVersion,draftValue=input.value,submittedFiles=[...files],revision=draftRevision;
    formRequestId ||= crypto.randomUUID();
    const submission={version,stopRequested:false,requestId:formRequestId,runId:run?.id||null};pendingSubmission=submission;refreshInput();
    followEnd=true;
    try{
      if(docked)await beforeStart?.();
      if(version!==dockContextVersion||submission.stopRequested)return;
      const canvasSelection=docked?getCanvasSelection?.():undefined;
      let data;
      if(run){data=await api(`/api/agent/runs/${run.id}/messages`,json({text,request_id:submission.requestId,asset_ids:submittedFiles.map(a=>a.id),values:{...studio.context(),intent_hint:intentSelect.value,...(canvasSelection?{canvas_selection:canvasSelection}:{})}}));}
      else{if(!docked)await beforeStart?.();if(version!==dockContextVersion||submission.stopRequested)return;data=await api('/api/agent/runs',json({text,workflow:'studio',intent_hint:intentSelect.value,request_id:submission.requestId,asset_ids:submittedFiles.map(a=>a.id),...(docked&&boundProjectId?{project_id:boundProjectId,canvas_selection:canvasSelection}:{}),preferences:{...prefs,canvas:false,scope:'full_video',mode}}));}
      submission.runId=data.run.id;
      if(version===dockContextVersion){
        if(!submission.stopRequested&&revision===draftRevision&&input.value===draftValue&&files.length===submittedFiles.length&&files.every((file,index)=>file.id===submittedFiles[index].id)){input.value='';files=[];renderAttached();}
        formRequestId=null;await applySnapshot(data);poll();
      }
      if(submission.stopRequested){
        await submission.stopPromise;
        if(version===dockContextVersion&&run?.id===submission.runId){const snapshot=await api(`/api/agent/runs/${encodeURIComponent(submission.runId)}`);if(version===dockContextVersion&&run?.id===submission.runId)await applySnapshot(snapshot);}
      }
    }catch(error){
      if(version===dockContextVersion){if(error.detail?.code==='generation_request_stopped')formRequestId=null;else toast(error.message);}
    }finally{
      if(pendingSubmission===submission)pendingSubmission=null;
      if(version===dockContextVersion){if(submission.stopRequested&&formRequestId===submission.requestId)formRequestId=null;refreshInput();}
    }
  }
  function renderAttached(){
    $('#agentAttached').innerHTML=files.map((a,index)=>`<span class="agent-reference" title="${esc(a.name)}" ${a.kind==='audio'?`data-audio-preview="${esc(a.url)}"`:''}>${a.kind==='video'?videoReferenceCover(a,esc):['image','prop'].includes(a.kind)?`<button type="button" class="reference-image-preview" data-agent-reference-preview="${esc(a.id)}" aria-label="放大预览 ${esc(a.name)}"><img src="${esc(a.url)}" alt="${esc(a.name)}" draggable="false"></button>`:a.kind==='audio'?'<span class="reference-audio-icon">♪</span>':icon('video')}<span class="reference-number">${index+1}</span>${a.kind==='audio'&&a.duration?`<span class="reference-duration">${Number(a.duration).toFixed(1)}s</span>`:''}<button type="button" data-remove="${esc(a.id)}" aria-label="移除 ${esc(a.name)}">×</button></span>`).join('');
    mentions.refresh();
  }
  async function assetPicker(){
    const assets=await api('/api/assets');
    const pane=modal('引用素材',`<input class="agent-search" placeholder="搜索素材名称" aria-label="搜索素材"><div class="agent-picker-grid"></div>`);
    const draw=()=>{const term=pane.querySelector('input').value.toLowerCase();pane.querySelector('.agent-picker-grid').innerHTML=assets.filter(a=>['image','prop','audio','video'].includes(a.kind)&&a.name.toLowerCase().includes(term)).map(a=>`<button data-pick="${esc(a.id)}">${a.kind==='image'||a.kind==='prop'?`<img src="${safeURL(a.url)}" alt="">`:a.kind==='audio'?'<span class="reference-audio-icon">♪</span>':icon('video')}<span>${esc(a.name)}</span><small>${files.some(f=>f.id===a.id)?'已引用':'引用素材'}</small></button>`).join('')||'<p class="agent-empty">暂无匹配素材，可以通过 + 上传。</p>';};
    pane.querySelector('input').oninput=draw;draw();pane.onclick=e=>{const b=e.target.closest('[data-pick]');if(!b)return;const a=assets.find(a=>a.id===b.dataset.pick);if(files.length>=20)return toast('最多引用20项素材');if(!files.some(f=>f.id===a.id))files.push(a);renderAttached();dialog.close();input.focus();};
  }
  async function history(){
    const runs=(await api('/api/agent/runs')).filter(item=>docked?item.id===run?.id:item.creation_type==='conversation');const pane=modal('创作记录',runs.length?`<div class="agent-history">${runs.map(r=>`<button data-run="${esc(r.id)}"><span><b>${esc(r.title)}</b><small>${timeLabel(r.updated_at)}</small></span><em>${esc(STATUS[r.status]||r.status)}</em>${icon('chevronRight')}</button>`).join('')}</div>`:'<p class="agent-empty">还没有创作记录，从一个故事开始吧。</p>');
    pane.onclick=async e=>{const b=e.target.closest('[data-run]');if(!b)return;const version=++dockContextVersion;try{dialog.close();await withLoading(async()=>{const snapshot=await api(`/api/agent/runs/${b.dataset.run}`);if(version===dockContextVersion)await applySnapshot(snapshot,true);},"正在读取创作记录…");if(version===dockContextVersion)poll();}catch(error){if(version===dockContextVersion)toast(error.message);}};
  }
  async function preferences(){
    capabilities=await api('/api/agent/capabilities');
    const pane=modal('创作偏好',`<div class="agent-preferences"><p>选择已有模型配置。模型密钥通过「模型管理」设置。</p>${['text','image','video','audio'].map((kind,i)=>`<label>${['导演与文本模型','图片生成模型','视频生成模型','音色、配音与 BGM'][i]}<select data-model="${kind}"><option value="">使用默认模型</option>${modelList(kind).map(m=>`<option value="${esc(m.id)}" ${prefs.models[kind]===m.id?'selected':''}>${esc(m.name)}${m.api_key_set?'':' · 未配置密钥'}</option>`).join('')}</select></label>`).join('')}<label>目标时长（秒，可留空自动分析）<input id="agentTargetDuration" type="number" min="8" max="600" value="${prefs.target_duration||''}"></label><div class="agent-preference-checks">${[['voice','制作正式配音'],['music','制作整片 BGM'],['subtitles','添加台词字幕']].map(([k,t])=>`<label><input type="checkbox" data-pref="${k}" ${prefs[k]?'checked':''}>${t}</label>`).join('')}</div><div class="agent-dialog-actions"><button class="agent-quiet" data-configure-models>模型管理</button><button class="agent-confirm" data-save-preferences>保存偏好</button></div></div>`);
    pane.querySelector('[data-configure-models]').onclick=()=>{dialog.close();onModels();};
    if(!run||run.engine==='studio-v1'){
      pane.querySelector('[data-model="audio"]').closest('label').hidden=true;
      pane.querySelector('.agent-preference-checks').hidden=true;
      const referenceNote=document.createElement('p');referenceNote.textContent='短剧视频直接引用已生成的人物、场景和必要道具素材，保持角色与环境一致。';
      pane.querySelector('.agent-dialog-actions').before(referenceNote);
    }
    pane.querySelector('[data-save-preferences]').onclick=async()=>{
      const next={models:{}};pane.querySelectorAll('[data-model]').forEach(s=>next.models[s.dataset.model]=s.value);pane.querySelectorAll('[data-pref]').forEach(c=>next[c.dataset.pref]=c.checked);
      if(!run||run.engine==='studio-v1')next.studio_video_mode='reference';
      const duration=Number(pane.querySelector('#agentTargetDuration').value);if(duration&&(!Number.isInteger(duration)||duration<8||duration>600))return toast('目标时长需为 8 至 600 的整数秒');next.target_duration=duration||null;
      const button=pane.querySelector('[data-save-preferences]');button.disabled=true;
      try{if(run){await applySnapshot(await api(`/api/agent/runs/${run.id}/messages`,json({request_id:crypto.randomUUID(),action:'preferences',values:next})));poll();}else Object.assign(prefs,next);
        dialog.close();syncQuickPreferences();
      }catch(error){toast(error.message);}finally{button.disabled=false;}
    };
  }
  const commands={
    new:async()=>{++dockContextVersion;clearTimeout(polling);workerStopping=false;run=null;cursor=0;files=[];input.value='';formRequestId=null;prefs.canvas=false;contextRuns.delete(contextKey);localStorage.removeItem(userStorageKey('jht.agent.lastRun'));$('#agentMessages').innerHTML='';root.classList.remove('with-canvas');setRun(null);renderAttached();input.focus();},
    history,assets:()=>mentions.open(input),upload:()=>$('#agentUpload').click(),preferences,
    models:()=>onModels(),
    skills:async()=>{capabilities ||= await api('/api/agent/capabilities');modal('创作技能',`<p class="agent-dialog-intro">以下技能已接入当前助手；画布信息会在每轮问答时重新读取。</p><div class="agent-skills">${((!run||run.engine==='studio-v1')?capabilities.studio?.skills||[]:capabilities.skills).filter(s=>s.id!=='canvas-management').map(s=>`<div>${icon('check')}<span><b>${esc(s.title||TITLES[s.id]||s.id)}</b><small>${esc(s.description)}</small></span></div>`).join('')}</div>`);},
  };
  root.addEventListener('click',async e=>{
    const preview=e.target.closest('[data-agent-reference-preview]');
    if(preview){const asset=files.find(item=>item.id===preview.dataset.agentReferencePreview);if(asset){mentions.close();referencePreview.open(asset);}return;}
    const c=e.target.closest('[data-command]'),action=e.target.closest('[data-action]'),remove=e.target.closest('[data-remove]'),artifact=e.target.closest('[data-artifact]'),tab=e.target.closest('[data-mode]');
    try{
      if(c){e.preventDefault();await commands[c.dataset.command]?.();}
      if(action)await act(action.dataset.action,{},action);
      if(remove){mentions.removeReference(remove.dataset.remove,input);files=files.filter(a=>a.id!==remove.dataset.remove);renderAttached();}
      if(tab){mode=tab.dataset.mode;root.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b===tab);b.setAttribute('aria-selected',String(b===tab));});}
      if(artifact){const a=await api(`/api/agent/runs/${run.id}/artifacts/${encodeURIComponent(artifact.dataset.artifact)}`);const pane=modal(a.title,`<article class="agent-document"></article><button class="agent-confirm" data-download-doc>下载文档</button>`);pane.querySelector('.agent-document').append(renderMarkdown(a.content));pane.querySelector('[data-download-doc]').onclick=()=>{const url=URL.createObjectURL(new Blob([a.content],{type:'text/markdown;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=a.title+'.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};}
    }catch(error){toast(error.message);}
  });
  $('#agentComposer').onsubmit=submit;input.oninput=()=>{draftRevision++;formRequestId=null;refreshInput();};
  input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(sendControl.getState()==='idle')$('#agentComposer').requestSubmit();}};
  $('#agentUpload').onchange=async e=>{try{for(const file of Array.from(e.target.files).slice(0,20-files.length)){await validateReferenceVideo(file);const data=new FormData();data.append('file',file);if(run)data.append('project_id',run.project_id);files.push(await api('/api/assets',{method:'POST',body:data}));}renderAttached();}catch(error){toast(error.message);}finally{renderAttached();e.target.value='';}};
  return {
    async setDocked(value,projectId=null,skipRestore=false){
      const ticket=++dockContextVersion;
      if(value){if(ticket!==dockContextVersion)return;}
      docked=value;boundProjectId=value?projectId:null;root.classList.toggle('agent-docked',value);
      const target=value?projectId:'standalone';
      if(contextKey!==target){
        drafts.set(contextKey,{text:input.value,files:[...files]});if(run)contextRuns.set(contextKey,run.id);
        contextKey=target;clearTimeout(polling);mentions.close();workerStopping=false;run=null;cursor=0;files=[];formRequestId=null;prefs.canvas=false;
        $('#agentMessages').innerHTML='';setRun(null);
        const draft=drafts.get(target);input.value=draft?.text||'';files=draft?.files||[];renderAttached();initialized=true;
        let id=contextRuns.get(target);
        let snapshot;
        if(value){
          const project=await api(`/api/projects/${encodeURIComponent(projectId)}`);
          if(ticket!==dockContextVersion)return;
          // The canvas belongs to its originating studio conversation. Never
          // replace that conversation with an unrelated legacy canvas Agent.
          const sourceId=project.studio_run_id||project.nodes?.find(node=>node.mujian?.runId)?.mujian.runId;
          if(sourceId){
            snapshot=await api(`/api/agent/runs/${encodeURIComponent(sourceId)}`);
            if(ticket!==dockContextVersion)return;
            if(snapshot.run.engine!=='studio-v1'||snapshot.run.studio?.canvas_project_id!==projectId)throw new Error('画布与创作对话的关联不一致，请重新打开画布');
            id=sourceId;
          }else{
            if(!id){const runs=(await api('/api/agent/runs')).filter(item=>item.creation_type==='canvas'&&item.project_id===projectId).sort((a,b)=>(b.updated_at||0)-(a.updated_at||0));id=runs[0]?.id;}
            if(id){snapshot=await api(`/api/agent/runs/${encodeURIComponent(id)}`);if(snapshot.run.engine!=='studio-v1'){id=null;snapshot=null;}}
          }
        }
        if(ticket!==dockContextVersion)return;
        if(id&&!skipRestore){const data=snapshot||await api(`/api/agent/runs/${encodeURIComponent(id)}`);if(ticket!==dockContextVersion)return;await applySnapshot(data,true);}
        if(visible&&run)poll();
      }
      setRun(run);refreshInput();
    },
    fillDraft(text){input.value=String(text);input.dispatchEvent(new Event('input',{bubbles:true}));input.focus({preventScroll:true});input.setSelectionRange(input.value.length,input.value.length);},
    focus(){input.focus({preventScroll:true});},
    showProduction(){studio.showProduction();},
    async newConversation(preserveDraft=false){initialized=true;++dockContextVersion;const saved={text:input.value,files:[...files]};await commands.new();if(preserveDraft){input.value=saved.text;files=saved.files;renderAttached();refreshInput();}},
    async openRun(id,snapshot=null,isCurrent=()=>true,preserveDraft=false){const data=snapshot||await api(`/api/agent/runs/${encodeURIComponent(id)}`);if(!isCurrent())return;if(data.run.creation_type!=='conversation')throw new Error('这是画布内的对话，请从画布打开');const retain=preserveDraft&&(run?.id===id||contextRuns.get('standalone')===id);initialized=true;await this.setDocked(false,null,true);if(!isCurrent())return;await applySnapshot(data,true);if(!retain){input.value='';files=[];}renderAttached();poll();},
    async show(){visible=true;try{capabilities ||= await api('/api/agent/capabilities');if(!initialized){initialized=true;const last=localStorage.getItem(userStorageKey('jht.agent.lastRun'));if(last){try{const data=await api(`/api/agent/runs/${encodeURIComponent(last)}`);if(data.run.creation_type==='conversation')await applySnapshot(data,true);}catch{localStorage.removeItem(userStorageKey('jht.agent.lastRun'));}}}if(run)poll();else refreshInput();}catch(error){toast(error.message);}},
    hide(){visible=false;clearTimeout(polling);quickPreferences.close();stylePicker.close();mentions.close();if(dialog.open)dialog.close();},
  };
}
