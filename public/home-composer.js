import { validateReferenceVideo, videoReferenceCover } from './reference-video.js?v=1';
import { attachAssetMentions } from './asset-mentions.js?v=studio-references-2';
import { createHomePreferences } from './home-preferences.js?v=selection-motion-10';
import { normalizeContentError } from './task-errors.js?v=content-rejection-3';
import { buildRegenerationRequest } from './generation-request.js?v=image-names-1';
import { createGenerationGuard } from './generation-state.js?v=request-stop-1';
import { createRequestButton } from './request-button.js?v=request-stop-1';
import { createImageLightbox } from './image-lightbox.js?v=1';
import { createVideoHoverPreview } from './video-hover-preview.js?v=1';
import { createVisualStylePicker } from './visual-style-picker.js?v=4';

const TYPES=[['agent','sparkle','Agent'],['image','imageNode','图片生成'],['video','play','视频生成'],['audio','audio','音频生成']];
const PLACEHOLDERS={image:'描述你想生成的画面，输入 @ 引用图片，或上传参考图…',video:'描述你的镜头、场景与动作，输入 @ 引用图片、视频或音频…',audio:'描述你想要的声音，或输入需要朗读的文案…'};
const pending=task=>['queued','running','waiting_tool'].includes(task.status);
const needsPoll=task=>pending(task)||task.video_cache_status==='pending';
const complete=task=>['done','succeeded'].includes(task.status);
const IMAGE_RATIOS=['adaptive','1:1','3:4','16:9','4:3','9:16','2:3','3:2','21:9'];
const VIDEO_RATIOS=['16:9','9:16','1:1','4:3','3:4','21:9'];

export function createHomeComposer({api,json,escapeHtml:esc,icon,toast,state,onConversations,onAssets,onModels,onConversation,onModeChange,onConversationReady,onPreviewAsset,onSaveAsset,onSaveTask,onRetryFile}) {
  const root=document.querySelector('#agentView'),wrap=root.querySelector('.agent-composer-wrap');
  let kind='agent',models=[],visible=false,pollTimer=null,polling=null,statePollError=false,loading=null,uploading=false,contentAnimations=[],navigationRevision=0;
  const drafts=Object.fromEntries(TYPES.filter(([key])=>key!=='agent').map(([key])=>[key,{generationScope:'draft:'+crypto.randomUUID(),prompt:'',visual_style:null,references:[],model:'',ratio:'16:9',imageRatio:'adaptive',imageSize:'2K',count:1,resolution:'768p',duration:4,videoMode:'all',frames:[null,null],audioMode:'sfx',format:'wav',speechRate:0,busy:false,requestId:null,conversationId:null}]));
  const preferences=createHomePreferences({root,icon,escapeHtml:esc});
  const stylePicker=createVisualStylePicker({api,escapeHtml:esc,icon,onError:toast});
  const referencePreview=createImageLightbox({onError:toast});
  const recentVideoPreview=createVideoHoverPreview();
  const jobs=[];
  root.classList.add('creation-home');root.dataset.homeMode=kind;
  const welcome=root.querySelector('.agent-welcome');
  welcome.innerHTML='<span class="home-eyebrow">建绘通 · AI 创作平台</span><h1>你好，今天想要<span>创作</span>什么？</h1><p>让一个想法，成为看得见、听得到的作品。</p>';
  const tabs=document.createElement('div');tabs.className='home-mode-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','首页创作功能');
  tabs.innerHTML=TYPES.map(([key,glyph,label])=>`<button type="button" role="tab" id="homeTab-${key}" data-home-mode="${key}" aria-selected="${key===kind}" tabindex="${key===kind?0:-1}">${icon(glyph)}${key==='agent'?'<span class="home-agent-wordmark" role="img" aria-label="Agent">Agent</span>':`<span>${label}</span>`}</button>`).join('');wrap.prepend(tabs);
  const thumb=document.createElement('i');thumb.className='home-mode-thumb';thumb.setAttribute('aria-hidden','true');tabs.prepend(thumb);
  function moveThumb(){const active=tabs.querySelector('[aria-selected=true]');if(active){thumb.style.width=active.offsetWidth+'px';thumb.style.height=active.offsetHeight+'px';thumb.style.transform=`translate(${active.offsetLeft}px,${active.offsetTop}px)`;}}
  new ResizeObserver(moveThumb).observe(tabs);document.fonts.ready.then(moveThumb);
  const form=document.createElement('form');form.id='homeMediaComposer';form.className='home-media-composer';form.hidden=kind==='agent';
  form.innerHTML=`<div id="homeReferences" class="home-references"></div><label class="sr-only" for="homePrompt">生成描述</label><textarea id="homePrompt" rows="4" maxlength="4000"></textarea><div class="home-media-tools"><button type="button" class="home-upload" aria-label="上传参考素材" title="上传参考素材">${icon('plus')}</button><button type="button" class="home-mention" aria-label="引用素材" title="引用素材">@</button><label class="home-model-control"><span class="sr-only">生成模型</span>${icon('cube')}<select id="homeModel" aria-label="生成模型"></select></label><div id="homeParameters" class="home-parameters"></div><button type="submit" class="home-enqueue" data-home-enqueue aria-label="加入队列" title="保留当前生成，将新请求加入队列" hidden>${icon('plus')}</button><button type="submit" class="home-send" aria-label="开始生成" disabled>${icon('arrowUp')}</button></div><div class="home-generation-error" role="status" hidden></div><input id="homeMediaUpload" type="file" multiple hidden></form>`;
  root.querySelector('#agentComposer').after(form);
  const results=document.createElement('section');results.className='home-results';results.hidden=true;results.setAttribute('aria-label','生成结果');wrap.after(results);
  const recent=document.createElement('section');recent.className='home-recent';wrap.parentElement.append(recent);
  const $=s=>form.querySelector(s),prompt=$('#homePrompt'),modelSelect=$('#homeModel'),error=$('.home-generation-error');
  const generation=createGenerationGuard({api,onChange:updateSend});
  const requestButton=createRequestButton($('.home-send'),{onStop:stopCurrent,onError:err=>{toast(err.message);updateSend();},sendLabel:'开始生成',stopLabel:'停止生成'});
  const draft=()=>drafts[kind];
  const generationScope=(d=draft())=>d?.viewConversationId||d?.conversationId||d?.generationScope;
  function newDraft(d){return {...d,generationScope:'draft:'+crypto.randomUUID(),busy:false,submissionId:null,title:'',prompt:'',references:[],videoMode:'all',frames:[null,null],requestId:null,conversationId:null,viewConversationId:null};}
  const modeControl=document.createElement('div');modeControl.className='home-conversation-mode';
  modeControl.innerHTML=`<select aria-label="对话生成模式">${TYPES.map(([value,,label])=>`<option value="${value}">${label}</option>`).join('')}</select>`;
  $('.home-media-tools').prepend(modeControl);
  const modeSelect=modeControl.querySelector('select');modeSelect.value=kind;
  preferences.button(modeControl,{label:()=>TYPES.find(([value])=>value===modeSelect.value)[2],groups:[{select:modeSelect,title:'生成模式'}],glyph:'sparkle'});
  modeSelect.onchange=()=>select(modeSelect.value,true);
  function animateFrame(index){
    if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    $('#homeReferences').querySelectorAll('.home-frame-slot').forEach((slot,i)=>{if(index===undefined||index===i)slot.animate([{opacity:0,transform:'translateY(5px) rotate(-7deg) scale(.94)'},{opacity:1,transform:'translateY(0) rotate(-7deg) scale(1)'}],{duration:240,easing:'cubic-bezier(.2,.75,.25,1)'});});
  }
  const frameMode=()=>kind==='video'&&draft().videoMode==='first-last';
  const referencesFor=(d,target=kind)=>target==='video'&&d.videoMode==='first-last'?d.frames.filter(Boolean):d.references;
  const accepts=(asset,target=kind)=>target==='image'||(target==='video'&&drafts.video.videoMode==='first-last')?['image','prop'].includes(asset.kind):['image','prop','audio','video'].includes(asset.kind);
  const limit=(target=kind)=>target==='image'?10:target==='audio'?3:frameMode()?2:20;
  const mentions=attachAssetMentions({root:form,selector:'#homePrompt',icon,escapeHtml:esc,onError:toast,
    onPreviewImage:asset=>referencePreview.open(asset),
    getReferences:()=>kind==='agent'?[]:referencesFor(draft()).map((a,i)=>({token:'@'+a.name,assetId:a.id,name:a.name,kind:a.kind,url:a.url,number:i+1})),
    getItems:async()=>(await api('/api/assets')).filter(asset=>accepts(asset)).map(asset=>({assetId:asset.id,name:asset.name,kind:asset.kind==='prop'?'image':asset.kind,url:asset.url,asset})),
    onSelect:async item=>{await validateReferenceVideo(item.asset);const d=draft(),refs=referencesFor(d);if(frameMode()){if(!d.frames.includes(null))throw new Error('首尾帧已选满，请先移除或替换图片');const index=d.frames.indexOf(null);return()=>{d.frames[index]=item.asset;d.requestId=null;drawReferences();animateFrame(index);updateSend();};}if(refs.length>=limit()&&!refs.some(a=>a.id===item.assetId))throw new Error(`最多引用 ${limit()} 项素材`);return()=>{if(!d.references.some(a=>a.id===item.assetId))d.references.push(item.asset);d.requestId=null;d.conversationId=null;drawReferences();};}
  });
  function drawReferences(){
    if(kind==='agent')return;
    prompt.placeholder=frameMode()?'描述首帧到尾帧的过渡、镜头运动和画面变化…':PLACEHOLDERS[kind];
    form.classList.toggle('is-first-last',frameMode());
    $('#homeReferences').classList.toggle('home-frame-slots',frameMode());
    if(frameMode()){
      $('#homeReferences').innerHTML=draft().frames.map((asset,index)=>`<span class="home-frame-slot"><button type="button" class="home-frame-pick" data-home-frame="${index}" aria-label="${asset?'替换':'上传'}${index?'尾帧':'首帧'}">${asset?`<img src="${esc(asset.url)}" alt="${esc(asset.name)}">`:icon('plus')}<span>${index?'尾帧':'首帧'}</span></button>${asset?`<button type="button" class="home-frame-remove" data-home-frame-remove="${index}" aria-label="移除${index?'尾帧':'首帧'}">×</button>`:''}</span>${index===0?`<button class="home-frame-swap" type="button" data-home-frame-swap aria-label="交换首尾帧"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h15l-3-3M20 16H5l3 3"/></svg></button>`:''}`).join('');
      mentions.refresh();return;
    }
    $('#homeReferences').innerHTML=draft().references.map((a,i)=>`<span class="home-reference" title="${esc(a.name)}">${a.kind==='video'?videoReferenceCover(a,esc):['image','prop'].includes(a.kind)?`<button type="button" class="reference-image-preview" data-home-reference-preview="${esc(a.id)}" aria-label="放大预览 ${esc(a.name)}"><img src="${esc(a.url)}" alt="${esc(a.name)}" draggable="false"></button>`:icon(a.kind==='audio'?'audio':'play')}<small>${i+1}</small><button type="button" data-home-remove="${esc(a.id)}" aria-label="移除 ${esc(a.name)}">×</button></span>`).join('');mentions.refresh();
  }
  function options(values,value){return values.map(item=>`<option value="${esc(item)}" ${String(item)===String(value)?'selected':''}>${esc(item)}</option>`).join('');}
  function drawModel(){
    if(kind==='agent')return;
    stylePicker.close();
    const d=draft(),list=models.filter(m=>m.kind===kind);
    let model=list.find(m=>m.id===d.model)||list.find(m=>m.is_default)||list[0];if(model)d.model=model.id;else if(!loading)d.model='';
    modelSelect.innerHTML=list.length?list.map(m=>`<option value="${esc(m.id)}" ${m.id===d.model?'selected':''}>${esc(m.name)}${m.api_key_set?'':' · 未配置'}</option>`).join(''):`<option value="">${loading?'正在读取模型…':'暂无模型'}</option>`;
    modelSelect.disabled=!!loading;
    const caps=model?.capabilities||{},params=$('#homeParameters');
    if(kind==='video'){
      const ratios=(caps.aspect_ratios?.length?caps.aspect_ratios:VIDEO_RATIOS).filter(v=>VIDEO_RATIOS.includes(v));
      const resolutions=caps.resolutions?.length?caps.resolutions:['720p','1080p'];
      const durations=(caps.durations?.length?caps.durations:Array.from({length:12},(_,i)=>i+4)).map(Number).filter(v=>v>=1&&v<=60);
      for(const [key,values] of [['ratio',ratios],['resolution',resolutions],['duration',durations]])if(!values.map(String).includes(String(d[key])))d[key]=values[0];
      params.innerHTML=`<select aria-label="视频画幅" data-param="ratio">${options(ratios,d.ratio)}</select><select aria-label="视频分辨率" data-param="resolution">${options(resolutions,d.resolution)}</select><select aria-label="视频时长" data-param="duration">${durations.map(v=>`<option value="${v}" ${v===d.duration?'selected':''}>${v} 秒</option>`).join('')}</select>`;
    }else if(kind==='image'){
      const sizes=caps.resolutions?.length?caps.resolutions:['1K','2K','4K'];
      if(!sizes.includes(d.imageSize))d.imageSize=sizes.includes('2K')?'2K':sizes[0];
      params.innerHTML=`<select aria-label="图片比例" data-param="imageRatio">${IMAGE_RATIOS.map(r=>`<option value="${r}" ${r===d.imageRatio?'selected':''}>${r==='adaptive'?'智能':r}</option>`).join('')}</select><select aria-label="图片分辨率" data-param="imageSize">${options(sizes,d.imageSize)}</select><select aria-label="图片数量" data-param="count">${[1,2,3,4].map(n=>`<option value="${n}" ${n===d.count?'selected':''}>${n} 张</option>`).join('')}</select>`;
    }
    else params.innerHTML=`<select aria-label="音频类型" data-param="audioMode">${[['sfx','音效'],['voice','配音'],['music','背景音乐']].map(([v,label])=>`<option value="${v}" ${v===d.audioMode?'selected':''}>${label}</option>`).join('')}</select><select aria-label="音频格式" data-param="format">${['wav','mp3','ogg_opus'].map(v=>`<option value="${v}" ${v===d.format?'selected':''}>${v==='ogg_opus'?'OGG Opus':v.toUpperCase()}</option>`).join('')}</select>${d.audioMode==='voice'?`<select aria-label="配音语速" data-param="speechRate">${[[-25,'0.75×'],[0,'1.0×'],[25,'1.25×'],[50,'1.5×'],[100,'2.0×']].map(([v,label])=>`<option value="${v}" ${v===d.speechRate?'selected':''}>${label}</option>`).join('')}</select>`:''}`;
    params.querySelectorAll('[data-param]').forEach(el=>el.onchange=()=>{d[el.dataset.param]=['duration','speechRate','count'].includes(el.dataset.param)?Number(el.value):el.value;d.requestId=null;d.conversationId=null;if(el.dataset.param==='audioMode'){preferences.close();drawModel();}});
    const controls=[...params.querySelectorAll('select')],group=(select,title,ratios=false)=>({select,title,ratios});
    if(kind==='video'){
      const videoMode=document.createElement('select');videoMode.setAttribute('aria-label','视频生成方式');videoMode.innerHTML='<option value="all">全能参考</option><option value="first-last">首尾帧</option>';videoMode.value=d.videoMode;params.prepend(videoMode);
      videoMode.onchange=()=>{d.videoMode=videoMode.value;d.requestId=null;preferences.close();drawReferences();updateSend();};
      preferences.button(params,{label:()=>d.videoMode==='first-last'?'首尾帧':'全能参考',groups:[{...group(videoMode,'视频生成方式'),menu:true}],glyph:'video'}).classList.add('home-video-mode-trigger');
      preferences.button(params,{label:()=>`${d.ratio} · ${d.resolution.toUpperCase()}`,groups:[group(controls[0],'选择比例',true),group(controls[1],'选择分辨率')],glyph:'imageNode'});
      preferences.button(params,{label:()=>`${d.duration}s`,groups:[group(controls[2],'选择时长')],glyph:'clock'}).classList.add('home-duration-trigger');
    }else if(kind==='image')preferences.button(params,{label:()=>`${d.imageRatio==='adaptive'?'智能比例':d.imageRatio} · ${d.imageSize} · ${d.count} 张`,groups:[group(controls[0],'选择比例',true),group(controls[1],'选择分辨率'),group(controls[2],'选择生成数量')],glyph:'imageNode'});
    else{
      preferences.button(params,{label:()=>controls[0].selectedOptions[0].textContent,groups:[group(controls[0],'音频类型')],glyph:'audio'});
      preferences.button(params,{label:()=>controls.slice(1).map(s=>s.selectedOptions[0].textContent).join(' · '),groups:controls.slice(1).map(s=>group(s,s.getAttribute('aria-label')))});
    }
    const modelControl=modelSelect.closest('.home-model-control');modelControl.querySelector('.home-preference-trigger')?.remove();modelControl.querySelector('.ui-icon')?.setAttribute('hidden','');
    if(kind!=='audio')stylePicker.button(params,{getValue:()=>d.visual_style,onChange:value=>{d.visual_style=value;d.requestId=null;d.conversationId=null;}});
    preferences.button(modelControl,{label:()=>modelSelect.selectedOptions[0]?.textContent||'暂无模型',groups:[group(modelSelect,'选择模型')],glyph:'sparkle'});
    updateSend();
  }
  function updateSend(){
    results.querySelectorAll('[data-home-regenerate]').forEach(button=>{const scope=jobs.find(job=>job.task.id===button.dataset.homeRegenerate)?.conversationId,blocked=generation.isBlocked(scope),reason=generation.reason(scope);button.disabled=blocked;button.title=reason||'使用本次生成的原始参数再次生成';button.setAttribute('aria-label',reason?`再次生成：${reason}`:'再次生成');});
    if(kind==='agent')return;
    const scope=generationScope(),blocked=generation.isBlocked(scope),reason=generation.reason(scope),status=generation.snapshot(scope);
    const disabled=blocked||uploading||draft().busy||!prompt.value.trim()||(frameMode()&&draft().frames.some(a=>!a));
    requestButton.setState(status.stopping?'stopping':status.active?'running':'idle',{disabled});
    const button=$('.home-send');button.classList.remove('is-busy');if(!status.active&&!status.stopping&&reason){button.setAttribute('aria-label',reason);button.title=reason;}
    const enqueue=$('.home-enqueue');enqueue.hidden=!status.active||!status.queueable;enqueue.disabled=disabled;
    form.setAttribute('aria-busy',String(status.active||status.stopping||blocked));
  }
  async function stopCurrent(){
    if(kind==='agent')return;
    const target=kind,d=draft(),scope=generationScope(d),requestId=generation.snapshot(scope).request?.request_id;
    const result=await generation.stop(scope);
    if(!result){updateSend();return;}
    if(d.submissionId===requestId){d.busy=false;d.submissionId=null;}
    if(d.requestId===requestId)d.requestId=null;
    for(const job of jobs)if(job.regenerationSubmissionId===requestId){job.regenerating=false;job.regenerationSubmissionId=null;job.regenerationRequestId=null;}
    for(const task of result.tasks||[]){const job=jobs.find(job=>job.task.id===task.id);if(job)Object.assign(job.task,task);}
    if(kind===target&&generationScope()===scope){error.hidden=true;updateSend();}
    if(result.message)toast(result.message);
    renderResults();poll();
  }
  function select(next,focus=false){
    if(!TYPES.some(([key])=>key===next))return;
    const changed=kind!==next;preferences.close();stylePicker.close();
    mentions.close();root.querySelectorAll('.home-results video,.home-results audio').forEach(el=>el.pause());
    kind=next;root.dataset.homeMode=next;
    modeSelect.value=next;modeControl.querySelector('button span').textContent=TYPES.find(([value])=>value===next)[2];
    root.querySelector('.agent-location span').textContent=next==='agent'?root.dataset.agentTitle||'新对话':draft().title||TYPES.find(([key])=>key===next)[2];
    tabs.querySelectorAll('[role=tab]').forEach(el=>{el.setAttribute('aria-selected',String(el.dataset.homeMode===next));el.tabIndex=el.dataset.homeMode===next?0:-1;});
    moveThumb();
    form.hidden=next==='agent';error.hidden=true;
    if(next!=='agent'){prompt.value=draft().prompt;prompt.maxLength=next==='audio'?3000:4000;prompt.placeholder=PLACEHOLDERS[next];$('#homeMediaUpload').accept=next==='image'?'image/*':'image/*,video/mp4,video/webm,audio/*';drawReferences();drawModel();if(focus)prompt.focus();}
    else if(focus)root.querySelector('#agentInput').focus();
    state.activeConversationId=next==='agent'?root.dataset.conversationId||null:draft().viewConversationId||null;onModeChange?.();renderResults();
    if(next!=='agent'){generation.requireRefresh(generationScope());poll();}
    if(changed&&!root.classList.contains('agent-docked')){
      const content=next==='agent'?root.querySelector('#agentComposer'):form;
      contentAnimations.forEach(animation=>animation.cancel());contentAnimations=[];
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches)contentAnimations=[...content.children].filter(el=>!el.hidden&&el.getClientRects().length).map((el,i)=>el.animate([{opacity:0,transform:'translateY(9px)'},{opacity:1,transform:'translateY(0)'}],{duration:320,delay:Math.min(i*35,105),easing:'cubic-bezier(.2,.7,.25,1)',fill:'backwards'}));
    }
  }
  tabs.onclick=e=>{const button=e.target.closest('[data-home-mode]');if(button)select(button.dataset.homeMode,true);};
  tabs.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const i=TYPES.findIndex(([key])=>key===kind),next=e.key==='Home'?0:e.key==='End'?3:(i+(e.key==='ArrowLeft'?3:1))%4;select(TYPES[next][0]);tabs.querySelector('[aria-selected=true]').focus();};
  prompt.oninput=()=>{draft().prompt=prompt.value;draft().requestId=null;draft().conversationId=null;error.hidden=true;updateSend();};
  prompt.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&!e.defaultPrevented){e.preventDefault();if(!generation.isActive(generationScope())&&!$('.home-send').disabled)form.requestSubmit();}});
  modelSelect.onchange=()=>{preferences.close();draft().model=modelSelect.value;draft().requestId=null;draft().conversationId=null;drawModel();};
  $('.home-mention').onclick=()=>mentions.open(prompt);$('.home-upload').onclick=()=>$('#homeMediaUpload').click();
  let frameTarget=0;
  const frameUpload=document.createElement('input');frameUpload.type='file';frameUpload.accept='image/*';frameUpload.hidden=true;frameUpload.id='homeFrameUpload';form.append(frameUpload);
  frameUpload.onchange=async()=>{const file=frameUpload.files[0],d=drafts.video,index=frameTarget;if(!file)return;uploading=true;updateSend();try{if(!file.type.startsWith('image/'))throw new Error('首尾帧仅支持图片');const data=new FormData();data.append('file',file);const asset=await api('/api/assets',{method:'POST',body:data});d.frames[index]=asset;d.requestId=null;if(kind==='video'){drawReferences();animateFrame(index);}}catch(err){toast(err.message);}finally{uploading=false;frameUpload.value='';updateSend();}};
  $('#homeReferences').onclick=e=>{
    const preview=e.target.closest('[data-home-reference-preview]');
    if(preview){const asset=draft().references.find(item=>item.id===preview.dataset.homeReferencePreview);if(asset){mentions.close();referencePreview.open(asset);}return;}
    const pick=e.target.closest('[data-home-frame]'),remove=e.target.closest('[data-home-frame-remove]');
    if(pick){frameTarget=Number(pick.dataset.homeFrame);frameUpload.click();return;}
    if(remove){const index=Number(remove.dataset.homeFrameRemove),asset=drafts.video.frames[index];if(asset&&!drafts.video.frames.some((other,i)=>i!==index&&other?.id===asset.id))mentions.removeReference(asset.id,prompt);drafts.video.frames[index]=null;drafts.video.requestId=null;drawReferences();updateSend();return;}
    if(e.target.closest('[data-home-frame-swap]')){
      const before=[...$('#homeReferences').querySelectorAll('.home-frame-slot')].map(slot=>slot.getBoundingClientRect());
      drafts.video.frames.reverse();drafts.video.requestId=null;drawReferences();
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches){
        $('#homeReferences').querySelectorAll('.home-frame-slot').forEach((slot,index)=>{const dx=before[1-index].left-slot.getBoundingClientRect().left;slot.animate([{transform:`translateX(${dx}px) rotate(-7deg) scale(.94)`,opacity:.7},{transform:`translateX(${dx*.35}px) translateY(${index?-8:8}px) rotate(0deg)`,opacity:1,offset:.65},{transform:'translateX(0) rotate(-7deg) scale(1)',opacity:1}],{duration:360,easing:'cubic-bezier(.2,.75,.25,1)'});});
        $('#homeReferences').querySelector('.home-frame-swap svg').animate([{transform:'rotate(0deg)'},{transform:'rotate(180deg)'}],{duration:360,easing:'cubic-bezier(.2,.75,.25,1)'});
      }
      return;
    }
    const button=e.target.closest('[data-home-remove]');if(button){mentions.removeReference(button.dataset.homeRemove,prompt);draft().references=draft().references.filter(a=>a.id!==button.dataset.homeRemove);draft().requestId=null;draft().conversationId=null;drawReferences();}};
  $('#homeMediaUpload').onchange=async e=>{
    const target=kind,d=draft();uploading=true;updateSend();
    try{for(const file of Array.from(e.target.files)){if(d.references.length>=limit(target))throw new Error(`最多引用 ${limit(target)} 项素材`);if(target==='image'&&!file.type.startsWith('image/'))throw new Error('图片生成仅支持引用图片');await validateReferenceVideo(file);const data=new FormData();data.append('file',file);d.references.push(await api('/api/assets',{method:'POST',body:data}));d.requestId=null;d.conversationId=null;}if(kind===target)drawReferences();}
    catch(err){toast(err.message);}finally{uploading=false;if(kind===target)drawReferences();e.target.value='';updateSend();}
  };
  const endpoint=job=>job.kind==='image'?'/api/images/generations':job.kind==='audio'?'/api/audio/generations':'/api/tasks';
  function metadata(job){
    const settings=job.settings||job.task.params||job.task;
    const model=models.find(m=>m.id===settings.model_config_id);
    const refs=job.references||[];
    const parameters=job.kind==='image'?[settings.aspect_ratio==='adaptive'?'智能比例':settings.aspect_ratio,settings.size,`${settings.count||1} 张`]:job.kind==='video'?[settings.aspect_ratio,settings.resolution?.toUpperCase(),settings.duration?`${settings.duration}s`:'']:[{sfx:'音效',voice:'配音',music:'背景音乐'}[settings.mode],settings.audio_config?.format?.toUpperCase()];
    const style=job.task.style_snapshot||job.task.params?.style_snapshot||settings.style_snapshot;
    if(style?.name)parameters.push(style.name);
    return {settings,refs,modelName:job.modelName||model?.name||job.task.model||TYPES.find(([key])=>key===job.kind)[2],parameters:parameters.filter(Boolean)};
  }
  function dateLabel(timestamp){const date=new Date((timestamp||Date.now()/1000)*1000),today=new Date();return date.toDateString()===today.toDateString()?'今天':date.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'});}
  function renderResults(){
    const matches=job=>kind!=='agent'&&job.kind===kind&&!!draft().viewConversationId&&job.conversationId===draft().viewConversationId;
    results.querySelectorAll('[data-home-job]').forEach(card=>{card.hidden=!jobs.some(job=>job.task.id===card.dataset.homeJob&&matches(job));});
    results.hidden=kind==='agent'||!jobs.some(matches);root.classList.toggle('has-media-conversation',!results.hidden);if(results.hidden)return;
    // Preserve an existing player while polling so playback and scrubbing remain stable.
    const relevant=jobs.filter(matches);
    for(const job of relevant){
      let card=results.querySelector(`[data-home-job="${CSS.escape(job.task.id)}"]`);
      syncResultLibraryButton(job,card);
      const meta=metadata(job),assets=resultAssets(job),deleted=resultDeleted(job),key=JSON.stringify([deleted,job.task.status,complete(job.task)?'':job.task.status_message,job.task.video_cache_status,job.task.error,assets.map(a=>a.url),job.task.video_url,job.task.progress,meta.modelName,meta.parameters]);
      if(card?.dataset.renderKey===key)continue;
      if(!card){card=document.createElement('article');card.dataset.homeJob=job.task.id;results.append(card);}
      card.hidden=false;
      card.dataset.renderKey=key;
      const task=job.task,url=assets[0]?.url||task.video_url||'';
      const images=job.kind==='image'?assets:[];
      const ratio=job.kind==='image'?meta.settings.aspect_ratio:job.kind==='video'?meta.settings.aspect_ratio:'1:1';
      card.style.setProperty('--result-ratio',/^\d+:\d+$/.test(ratio||'')?ratio.replace(':','/'):'1');
      const progress=typeof task.progress==='number'&&Number.isFinite(task.progress)?`${Math.max(0,Math.min(100,Math.round(task.progress)))}% · `:'';
      const preview=deleted?`<div class="home-deleted-media" role="group" aria-label="${esc(deletedResultLabel(job))}"><span class="home-deleted-media-icon" aria-hidden="true">${icon(job.kind)}</span><strong>${esc(deletedResultLabel(job))}</strong><p>原素材已删除，提示词与生成参数仍保留</p></div>`:images.length?`<div class="home-image-results">${images.map((a,i)=>`<button type="button" class="home-image-preview" data-home-preview-index="${i}" aria-label="查看第 ${i+1} 张生成图片"><img src="${esc(a.url)}" alt="${esc(job.prompt)} · ${i+1}" draggable="false"></button>`).join('')}</div>${!complete(task)?`<div class="home-task-state">${esc(pending(task)?`正在生成 ${images.length}/${task.count||1} 张…`:normalizeContentError(task.error)||'部分图片生成未完成')}</div>`:''}`:complete(task)&&url?(job.kind==='audio'?`<audio controls preload="metadata" src="${esc(url)}"></audio>`:`<video data-conversation-video controls playsinline preload="metadata" src="${esc(url)}"></video>`):pending(task)?`<div class="home-generation-placeholder"><span>${progress}${task.queue_state==='waiting'?'排队中':task.queue_state==='credentials'?'等待授权':'生成中'}</span><i aria-hidden="true"></i></div><p class="home-generation-status" role="status">${esc(task.status_message||'正在生成，请稍候')}</p>`:`<div class="home-task-state">${icon('warning')}<span>${esc(normalizeContentError(task.error)||'生成未完成')}</span></div>`;
      card.innerHTML=`<h2 class="home-result-date">${esc(dateLabel(task.created_at))}</h2><header class="home-result-summary"><div class="home-result-references">${meta.refs.map((a,i)=>a.url&&['image','prop'].includes(a.kind||'image')?`<button type="button" class="home-reference-preview" data-home-reference-index="${i}" title="${esc(a.name)}" aria-label="预览参考图片：${esc(a.name||'图片')}"><img src="${esc(a.url)}" alt="${esc(a.name||'参考图片')}" draggable="false"></button>`:`<span title="${esc(a.name)}">${a.kind==='video'?videoReferenceCover(a,esc):icon(a.kind==='audio'?'audio':'video')}</span>`).join('')}</div><p class="home-user-prompt">${esc(job.prompt)}</p><div class="home-result-meta"><span>${esc(meta.modelName)}</span>${meta.parameters.map(v=>`<span>${esc(v)}</span>`).join('')}</div><button type="button" data-home-details="${esc(task.id)}">详细信息 ${icon('help')}</button></header>${preview}${deleted||complete(task)||images.length?`${deleted?'':`<p class="home-turn-note">${esc(task.video_cache_status==='failed'?'生成已完成，文件下载失败，可重试下载':task.video_cache_status==='pending'?'已加入生成历史，正在下载视频文件':'已加入生成历史 · 内容由 AI 生成')}</p>`}<footer class="home-turn-actions">${deleted?'':saveResultButton(job)}${!deleted&&job.kind==='video'&&task.video_cache_status==='failed'?`<button type="button" data-home-retry-file="${esc(task.id)}">重试下载</button>`:''}<button type="button" data-home-edit="${esc(task.id)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4H6a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h7M17 3l4 4-9 9-5 1 1-5 9-9Z"/></svg>重新编辑</button><button type="button" data-home-regenerate="${esc(task.id)}">${icon('regenerate')}再次生成</button><button type="button" class="home-turn-delete" data-home-delete="${esc(task.id)}" aria-label="删除本段对话及生成内容" title="删除本段对话及生成内容">${icon('trash')}</button></footer>`:''}`;
    }
    let previousDate='';results.querySelectorAll('article:not([hidden])').forEach(card=>{const date=card.querySelector('.home-result-date');date.hidden=date.textContent===previousDate;previousDate=date.textContent;});
    // Switch layouts only after the conversation has visible task content.
    onConversationReady?.();
    updateSend();
  }
  const libraryJobs=new Set();
  const deletedTaskAssets=new Map();
  function resultAssets(job) {
    const task=job.task,assets=task.assets?.length?task.assets:task.asset?[task.asset]:[];
    return assets.filter(asset=>!asset.deleted_at&&!task.deleted_asset_ids?.includes(asset.id)&&!deletedTaskAssets.get(task.id)?.has(asset.id));
  }
  function resultDeleted(job) {
    const task=job.task;
    if(job.kind==='video')return deletedTaskAssets.has(task.id)||task.asset_save_status==='deleted'||task.video_cache_status==='deleted';
    return !pending(task)&&!resultAssets(job).length&&!!(deletedTaskAssets.has(task.id)||task.deleted_asset_ids?.length||task.asset?.deleted_at||task.assets?.some(asset=>asset.deleted_at));
  }
  function deletedResultLabel(job) {
    return ({image:'图片',audio:'音频',video:'视频'}[job.kind]||'素材')+'已删除';
  }
  function resultSaved(job) {
    const task=job.task, assets=resultAssets(job);
    return job.kind==='video'?task.asset_save_status==='saved':!!assets.length&&assets.every(asset=>asset.in_library);
  }
  function saveResultButton(job) {
    const saved=resultSaved(job),busy=libraryJobs.has(job.task.id);
    return `<button type="button" data-home-save="${esc(job.task.id)}" data-player-library data-library-saved="${saved}" aria-busy="${busy}" ${saved||busy?'disabled':''}>${icon('library')}<span>${busy?'正在保存…':saved?'已保存至资产库':'保存至资产库'}</span></button>`;
  }
  function syncResultLibraryButton(job,card) {
    const button=card?.querySelector('[data-home-save]');if(!button)return;
    const saved=resultSaved(job),busy=libraryJobs.has(job.task.id);
    button.disabled=saved||busy;
    button.dataset.librarySaved=String(saved);
    button.setAttribute('aria-busy',String(busy));
    const label=button.querySelector('span'),text=busy?'正在保存…':saved?'已保存至资产库':'保存至资产库';
    if(label&&label.textContent!==text)label.textContent=text;
  }
  async function saveResult(job,button) {
    if(libraryJobs.has(job.task.id)||resultSaved(job)||resultDeleted(job))return;
    const assets=resultAssets(job);
    libraryJobs.add(job.task.id);
    syncResultLibraryButton(job,button.closest('[data-home-job]'));
    try {
      if(job.kind==='video')await onSaveTask?.(job.task,true);
      else for(const asset of assets){
        if(asset.in_library)continue;
        const saved=await onSaveAsset?.(asset,true);if(saved)Object.assign(asset,saved);
      }
    }catch(error){toast(error.message);}
    finally{libraryJobs.delete(job.task.id);renderResults();}
  }
  document.addEventListener('asset-library-change',({detail:asset})=>{
    let changed=false;
    for(const job of jobs){
      for(const item of [...(job.task.assets||[]),job.task.asset].filter(Boolean)){
        if(item.id===asset.id){Object.assign(item,asset);changed=true;}
      }
      if(job.kind==='video'&&(job.task.asset_id===asset.id||job.task.id===asset.task_id)){
        job.task.asset_id=asset.id;job.task.asset_save_status=asset.in_library?'saved':'history';changed=true;
      }
    }
    if(changed)renderResults();
  });
  document.addEventListener('asset-deleted',({detail:asset})=>{
    let changed=false;
    for(const job of jobs){
      if(job.task.asset_id===asset.id||job.task.id===asset.task_id||[...(job.task.assets||[]),job.task.asset].some(item=>item?.id===asset.id)){
        const removed=deletedTaskAssets.get(job.task.id)||new Set();
        removed.add(asset.id);deletedTaskAssets.set(job.task.id,removed);
        if(job.kind==='video'){job.task.asset_save_status='deleted';job.task.video_cache_status='deleted';}
        changed=true;
      }
    }
    if(changed)renderResults();
  });
  function editTurn(job) {
    const d=drafts[job.kind];
    d.prompt=job.prompt;
    d.visual_style=structuredClone(metadata(job).settings.visual_style||null);
    d.requestId=null;d.conversationId=null;
    select(job.kind);
    prompt.value=job.prompt;
    prompt.dispatchEvent(new Event('input',{bubbles:true}));
    mentions.refresh();
    prompt.setSelectionRange(prompt.value.length,prompt.value.length);
    prompt.focus({preventScroll:true});
  }
  async function regenerateTurn(job,button) {
    const scope=job.conversationId;
    const requestId=job.regenerationRequestId ||= crypto.randomUUID();
    if(job.regenerating||!generation.beginSubmission(scope,{requestId,kind:job.kind}))return;
    job.regenerating=true;job.regenerationSubmissionId=requestId;button.disabled=true;
    const revision=navigationRevision;
    let submittedTask;
    try {
      const settings=structuredClone(metadata(job).settings);
      const body=buildRegenerationRequest({...job,settings},requestId);
      const task=await api(endpoint(job),json(body));
      submittedTask=task;if(job.regenerationRequestId===requestId)job.regenerationRequestId=null;
      if(!jobs.some(j=>j.task.id===task.id))jobs.push({kind:job.kind,task,prompt:job.prompt,conversationId:job.conversationId,settings,modelName:job.modelName,references:[...(job.references||[])]});
      if(revision===navigationRevision&&drafts[job.kind].viewConversationId===job.conversationId)await onConversations(job.conversationId);
      renderResults();
    }catch(err){if(!generation.wasStopped(scope,requestId))toast(err.message);}
    finally{if(job.regenerationSubmissionId===requestId){job.regenerating=false;job.regenerationSubmissionId=null;}generation.finishSubmission(scope,submittedTask,job.kind,job.conversationId,requestId);poll();}
  }
  async function deleteTurn(job,button) {
    if(job.deleting)return;
    job.deleting=true;button.disabled=true;
    try {
      const deleted=await api(`/api/conversations/${encodeURIComponent(job.conversationId)}/tasks/${encodeURIComponent(job.task.id)}`,{method:'DELETE'});
      results.querySelector(`[data-home-job="${CSS.escape(job.task.id)}"]`)?.remove();
      const index=jobs.indexOf(job);if(index>=0)jobs.splice(index,1);
      state.assets=(state.assets||[]).filter(asset=>!deleted.deleted_asset_ids.includes(asset.id));
      if(deleted.conversation_deleted){
        for(const [key,d] of Object.entries(drafts))if(d.viewConversationId===job.conversationId||d.conversationId===job.conversationId)drafts[key]={...d,generationScope:'draft:'+crypto.randomUUID(),busy:false,viewConversationId:null,conversationId:null,title:'',requestId:null};
        if(state.activeConversationId===job.conversationId){state.activeConversationId=null;const url=new URL(location.href);url.searchParams.delete('conversation');history.replaceState(null,'',url);}
      }
      await onConversations();
      renderRecent();renderResults();onModeChange?.();
      toast('已删除本段对话及生成内容');
    }catch(err){toast(err.message);}
    finally{job.deleting=false;if(button.isConnected)button.disabled=false;}
  }
  const details=document.createElement('dialog');details.className='home-result-details';details.innerHTML='<header><h2>生成详情</h2><button type="button" aria-label="关闭详情">×</button></header><div></div>';root.append(details);
  details.querySelector('button').onclick=()=>details.close();details.onclick=e=>{if(e.target===details)details.close();};
  results.onclick=async e=>{
    const save=e.target.closest('[data-home-save]');
    if(save){const job=jobs.find(item=>item.task.id===save.dataset.homeSave);if(job)await saveResult(job,save);return;}
    const retry=e.target.closest('[data-home-retry-file]');
    if(retry){
      const job=jobs.find(item=>item.task.id===retry.dataset.homeRetryFile);if(!job)return;retry.disabled=true;
      try{const asset=await api(`/api/tasks/${encodeURIComponent(job.task.id)}/history-asset`,json({}));const result=await onRetryFile?.(asset);if(result){job.task.video_url=result.url;job.task.video_cache_status=result.file_status;}}
      catch(error){toast(error.message);}finally{retry.disabled=false;renderResults();}return;
    }
    const reference=e.target.closest('[data-home-reference-index]');
    if(reference){const job=jobs.find(item=>item.task.id===reference.closest('[data-home-job]')?.dataset.homeJob),asset=job?.references?.[Number(reference.dataset.homeReferenceIndex)];if(asset)referencePreview.open(asset);return;}
    const preview=e.target.closest('[data-home-preview-index]');
    if(preview){const job=jobs.find(item=>item.task.id===preview.closest('[data-home-job]')?.dataset.homeJob),assets=job?resultAssets(job):[],asset=assets[Number(preview.dataset.homePreviewIndex)];if(asset)onPreviewAsset?.(asset);return;}
    const action=e.target.closest('[data-home-edit],[data-home-regenerate],[data-home-delete]');
    if(action){
      const id=action.dataset.homeEdit||action.dataset.homeRegenerate||action.dataset.homeDelete;
      const job=jobs.find(j=>j.task.id===id);if(!job)return;
      if(action.hasAttribute('data-home-edit'))editTurn(job);
      else if(action.hasAttribute('data-home-regenerate'))regenerateTurn(job,action);
      else deleteTurn(job,action);
      return;
    }
    if(e.target.closest('[data-home-assets]'))onAssets();const button=e.target.closest('[data-home-details]');if(button){const job=jobs.find(j=>j.task.id===button.dataset.homeDetails);if(!job)return;const meta=metadata(job);details.querySelector('div').innerHTML=`<dl><dt>模型</dt><dd>${esc(meta.modelName)}</dd><dt>生成参数</dt><dd>${esc(meta.parameters.join(' · ')||'默认')}</dd><dt>状态</dt><dd>${esc(resultDeleted(job)?deletedResultLabel(job):pending(job.task)?'生成中':complete(job.task)?'已完成':normalizeContentError(job.task.error)||'生成失败')}</dd><dt>提示词</dt><dd>${esc(job.prompt)}</dd>${meta.refs.length?`<dt>参考素材</dt><dd>${meta.refs.map(a=>esc(a.name)).join('、')}</dd>`:''}</dl>`;details.showModal();}};
  async function refreshGenerationState(scope){
    try{await generation.refresh(scope);if(scope===generationScope())statePollError=false;}
    catch(err){if(scope===generationScope()&&!statePollError){toast(err.message);statePollError=true;}}
  }
  async function poll(){
    clearTimeout(pollTimer);if(!visible)return;
    const scope=generationScope(),conversationId=draft()?.viewConversationId;
    // Media polling and send availability are independent. A slow video GET
    // must not hold another conversation's state check or its refresh timer.
    if(!polling||polling.scope!==scope){
      const cycle=(async()=>{
        await Promise.allSettled(jobs.filter(job=>job.conversationId===conversationId&&needsPoll(job.task)).map(async job=>{try{job.task=await api(`${endpoint(job)}/${encodeURIComponent(job.task.id)}`,{background:true});job.pollError=false;}catch(err){if(!job.pollError){toast(err.message);job.pollError=true;}}}));
        renderResults();updateSend();
      })();
      cycle.scope=scope;polling=cycle;
      cycle.finally(()=>{if(polling===cycle)polling=null;});
    }
    await refreshGenerationState(scope);
    if(visible&&scope===generationScope()&&(generation.isActive(scope)||generation.isBlocked(scope)||jobs.some(job=>job.conversationId===conversationId&&needsPoll(job.task)))){
      clearTimeout(pollTimer);pollTimer=setTimeout(poll,1800);
    }
  }
  function refreshGenerationOnReturn(){
    if(!visible||document.hidden)return;
    generation.requireRefresh(generationScope());poll();
  }
  window.addEventListener('focus',refreshGenerationOnReturn);
  window.addEventListener('online',refreshGenerationOnReturn);
  document.addEventListener('visibilitychange',refreshGenerationOnReturn);
  form.onsubmit=async e=>{
    e.preventDefault();if(kind==='agent')return;const target=kind,d=draft(),scope=generationScope(d),text=prompt.value.trim(),revision=navigationRevision;
    const enqueue=e.submitter?.hasAttribute('data-home-enqueue');
    if(!text||d.busy||uploading||generation.isBlocked(scope)||generation.isActive(scope)&&!enqueue)return;if(frameMode()&&d.frames.some(a=>!a)){error.textContent='请上传首帧和尾帧图片';error.hidden=false;return;}
    const model=models.find(m=>m.id===d.model&&m.kind===target);if(!model?.api_key_set){error.textContent='请先在模型管理中配置当前模型的 API Key。';error.hidden=false;return;}
    d.requestId ||= crypto.randomUUID();
    if(!generation.beginSubmission(scope,{requestId:d.requestId,kind:target}))return;
    d.busy=true;d.submissionId=d.requestId;updateSend();error.hidden=true;
    // Capture the entire request so switching tabs cannot change an in-flight submission.
    const captured={...d,visual_style:structuredClone(d.visual_style||null),references:[...referencesFor(d,target)]},references=captured.references.map(a=>a.id);
    let submittedTask,submittedConversationId=d.viewConversationId||d.conversationId;
    try{
      let task,conversationId='';
      const audioConfig={format:captured.format,speech_rate:captured.audioMode==='voice'?captured.speechRate:0};
      const settings=target==='video'?{visual_style:captured.visual_style,prompt:text,negative_prompt:'',model_config_id:model.id,model:model.model_id,aspect_ratio:captured.ratio,resolution:captured.resolution,duration:captured.duration,asset_ids:references,mode:captured.videoMode==='first-last'?'first-last':references.length?'image-to-video':'text-to-video'}:{model_config_id:model.id,reference_asset_ids:references,...(target==='audio'?{mode:captured.audioMode,audio_config:audioConfig}:{size:captured.imageSize,aspect_ratio:captured.imageRatio,count:captured.count,visual_style:captured.visual_style})};
      conversationId=captured.viewConversationId||captured.conversationId;
      if(!conversationId){const conversation=await api('/api/conversations',json({kind:target,prompt:text,settings,request_id:captured.requestId}));conversationId=conversation.id;submittedConversationId=conversationId;
        if(generation.wasStopped(scope,captured.requestId))return;
        generation.bindConversation(scope,conversationId,captured.requestId);if(revision===navigationRevision){if(d.requestId===captured.requestId)d.conversationId=conversation.id;d.title=conversation.title;if(kind===target)root.querySelector('.agent-location span').textContent=d.title;}await onConversations(revision===navigationRevision?conversation.id:undefined);}
      if(generation.wasStopped(scope,captured.requestId))return;
      if(target==='video')task=await api('/api/tasks',json({...settings,project_id:conversationId,node_id:'conversation-video',request_id:captured.requestId}));
      else task=await api(target==='image'?'/api/images/generations':'/api/audio/generations',json({prompt:text,project_id:conversationId,model_config_id:model.id,reference_asset_ids:references,request_id:captured.requestId,...(target==='image'?{size:captured.imageSize,aspect_ratio:captured.imageRatio,count:captured.count,watermark:false,visual_style:captured.visual_style}:{name:text.slice(0,60),mode:captured.audioMode,audio_config:audioConfig})}));
      submittedTask=task;submittedConversationId=conversationId;
      if(!jobs.some(job=>job.task.id===task.id))jobs.push({kind:target,task,prompt:text,conversationId,settings,modelName:model.name,references:[...captured.references]});
      if(generation.wasStopped(scope,captured.requestId)){renderResults();return;}
      if(revision===navigationRevision)d.viewConversationId=conversationId;
      const unchanged=d.requestId===captured.requestId;if(unchanged){d.requestId=null;d.conversationId=null;}
      if(revision===navigationRevision&&unchanged){
        d.prompt='';d.references=[];d.frames=[null,null];
        if(kind===target){prompt.value='';drawReferences();mentions.refresh();}
      }
      renderResults();
      if(revision===navigationRevision&&kind===target)results.scrollTo({top:results.scrollHeight,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
    }catch(err){if(!generation.wasStopped(scope,captured.requestId)){if(revision===navigationRevision&&kind===target){error.textContent=err.message;error.hidden=false;}else toast(err.message);}}
    finally{if(d.submissionId===captured.requestId){d.busy=false;d.submissionId=null;}generation.finishSubmission(scope,submittedTask,target,submittedConversationId,captured.requestId);poll();}
  };
  function renderRecent(){
    recentVideoPreview.stop();
    const conversations=(state.conversations||[]).slice(0,4);recent.hidden=!conversations.length;
    recent.innerHTML=`<header><h2>最近对话</h2></header><div>${conversations.map(item=>`<button type="button" data-home-conversation="${esc(item.id)}"><span class="home-project-art">${item.cover?.url?(item.cover.kind==='video'?`<video data-media-src="${esc(String(item.cover.url).split('#')[0])}#t=0.05" muted loop playsinline preload="none" aria-label="视频预览"></video>`:`<img src="${esc(item.cover.url)}" alt="" loading="lazy">`):icon(item.kind==='agent'?'sparkle':item.kind==='image'?'imageNode':item.kind==='audio'?'audio':'play')}</span><strong>${esc(item.title||item.name)}</strong><small>${new Date(item.updated_at*1000).toLocaleDateString('zh-CN')}</small></button>`).join('')}</div>`;
    recentVideoPreview.bind(recent.querySelectorAll('[data-home-conversation]'));
  }
  recent.onclick=e=>{const item=e.target.closest('[data-home-conversation]');if(item){recentVideoPreview.stop();onConversation(item.dataset.homeConversation);}};
  root.addEventListener('click',e=>{
    if(root.classList.contains('agent-docked')||!e.target.closest('[data-command=new]'))return;
    navigationRevision++;
    if(kind==='agent'){state.activeConversationId=null;onModeChange?.();return;}
    e.preventDefault();e.stopImmediatePropagation();drafts[kind]=newDraft(draft());state.activeConversationId=null;select(kind,true);onConversations();
  },true);
  return {
    resetHome(){navigationRevision++;preferences.close();mentions.close();if(details.open)details.close();for(const [key,d] of Object.entries(drafts)){if(d.viewConversationId||d.conversationId||d.busy||generation.isBlocked(generationScope(d)))drafts[key]=newDraft(d);}select('agent');state.activeConversationId=null;onModeChange?.();root.scrollTop=0;},
    async show(){visible=true;generation.requireRefresh(generationScope());renderRecent();if(!loading)loading=api('/api/model-registry').then(data=>{models=data.models||[];drawModel();}).catch(err=>toast(err.message)).finally(()=>{loading=null;drawModel();});drawModel();await Promise.all([loading,poll(),onConversations().then(()=>{if(visible)renderRecent();}).catch(err=>toast(err.message))]);},
    hide(){stylePicker.close();recentVideoPreview.stop();visible=false;clearTimeout(pollTimer);preferences.close();if(details.open)details.close();mentions.close();results.querySelectorAll('video,audio').forEach(el=>el.pause());},
    async openConversation(id,isCurrent=()=>true){
      generation.requireRefresh(id);
      const revision=++navigationRevision,item=await api(`/api/conversations/${encodeURIComponent(id)}`);
      if(revision!==navigationRevision||!isCurrent())return;
      if(!drafts[item.kind])return;const d=newDraft(drafts[item.kind]);
      const settings=item.settings||{};
      Object.assign(d,{visual_style:structuredClone(settings.visual_style||null),title:item.title,prompt:'',references:[],frames:[null,null],requestId:null,conversationId:null,viewConversationId:id,model:settings.model_config_id||'',ratio:settings.aspect_ratio||'16:9',imageRatio:settings.aspect_ratio||'adaptive',imageSize:settings.size||'2K',count:settings.count||1,resolution:settings.resolution||'720p',duration:settings.duration||5,audioMode:settings.mode||'sfx',format:settings.audio_config?.format||'wav',speechRate:settings.audio_config?.speech_rate||0});
      const assets=await api('/api/assets');if(revision!==navigationRevision||!isCurrent())return;
      drafts[item.kind]=d;
      if(item.kind==='video')d.videoMode=settings.mode==='first-last'?'first-last':'all';
      for(const task of [...(item.tasks||[])].sort((a,b)=>(a.created_at||0)-(b.created_at||0))){
        const turnSettings=task.generation_settings||{...settings,...(task.params||{}),...Object.fromEntries(['model_config_id','size','aspect_ratio','count','watermark','mode','audio_config','reference_asset_ids','visual_style','style_snapshot'].filter(key=>task[key]!==undefined).map(key=>[key,task[key]]))};
        const existing=jobs.find(job=>job.task.id===task.id);
        const updated={kind:item.kind,task,prompt:task.prompt??task.params?.prompt??item.prompt,conversationId:id,settings:turnSettings,references:(turnSettings.reference_asset_ids||turnSettings.asset_ids||[]).map(aid=>assets.find(a=>a.id===aid)).filter(Boolean)};
        if(!existing)jobs.push(updated);
        else if(!(pending(task)&&!pending(existing.task))&&(task.updated_at||0)>=(existing.task.updated_at||0))Object.assign(existing,updated);
      }
      const latest=jobs.filter(job=>job.conversationId===id).at(-1);
      if(latest)d.visual_style=structuredClone(latest.settings.visual_style||null);
      select(item.kind,true);poll();
    },
    select
  };
}
