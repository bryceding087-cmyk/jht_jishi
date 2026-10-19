import { setSurfaceVisible } from './canvas-motion.js?v=liblib-detail-2';
import { createNodeVideoControls } from './node-video-controls.js?v=combined-output-3';

// Node attachments use world-sized geometry projected through the canvas view.
export function createNodeContext(api, canvas, inspector, refresh) {
  const { state, icon, escapeHtml: esc } = api;
  const $ = selector => document.querySelector(selector);
  const toolbar = document.createElement('div');
  toolbar.id = 'nodeContextToolbar';
  toolbar.className = 'workspace-chrome node-context-toolbar';
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', '选中节点操作');
  toolbar.hidden = true;
  canvas.append(toolbar);
  const popover = document.createElement('div');
  popover.className = 'workspace-chrome node-option-popover';
  popover.id = 'nodeOptionPopover';
  popover.hidden = true;
  canvas.append(popover);
  let activeSelect = null, anchor = null, toolbarKey = '', selectedId = '', busy = false;
  const controls = [];
  const videoControls=createNodeVideoControls(api,inspector,refresh);
  const glyphs = {modelSelect:'sparkle',ratioSelect:'preview',resolutionSelect:'fit',durationSelect:'clock'};
  const labels = {modelSelect:'生成模型',ratioSelect:'画面比例',resolutionSelect:'清晰度',durationSelect:'视频时长'};
  function closeOptions() {
    setSurfaceVisible(popover, false);
    anchor?.setAttribute('aria-expanded','false');
    activeSelect = null; anchor = null;
  }
  function positionOptions() {
    if (!anchor || popover.hidden) return;
    const bounds = canvas.getBoundingClientRect(), r = anchor.getBoundingClientRect();
    popover.style.transformOrigin='top left';
    popover.style.transform=`scale(${state.zoom})`;
    popover.style.left=(r.left-bounds.left)+'px';
    popover.style.top=(r.top-bounds.top-(popover.offsetHeight+8)*state.zoom)+'px';
  }
  function renderOptions() {
    if (!activeSelect) return;
    const select = activeSelect;
    popover.setAttribute('role','listbox');
    popover.setAttribute('aria-label',labels[select.id]);
    popover.dataset.optionKind = select.id;
    popover.innerHTML = `<div class="node-option-heading">${labels[select.id]}</div><div class="node-option-grid">${[...select.options].map((option,index)=>`<button type="button" role="option" data-option="${index}" aria-selected="${option.selected}" ${option.disabled?'disabled':''}>${select.id==='ratioSelect'?`<i class="ratio-outline" style="--ratio:${({ '16:9':'16 / 9','9:16':'9 / 16','1:1':'1','4:3':'4 / 3','3:4':'3 / 4','21:9':'21 / 9'})[option.value]||'1'}"></i>`:''}<span>${esc(option.textContent)}</span>${option.selected?icon('check'):''}</button>`).join('')}</div>`;
    popover.querySelectorAll('[data-option]').forEach(button => button.onclick = () => {
      select.selectedIndex = Number(button.dataset.option);
      select.dispatchEvent(new Event('change',{bubbles:true}));
      closeOptions(); sync();
    });
  }
  for (const id of Object.keys(labels)) {
    if(['modelSelect','ratioSelect','resolutionSelect'].includes(id))continue;
    const select = $('#'+id), button = document.createElement('button');
    button.type = 'button'; button.className = 'composer-option'; button.id = id+'Trigger';
    button.setAttribute('aria-label',labels[id]);
    button.setAttribute('aria-haspopup','listbox'); button.setAttribute('aria-expanded','false');
    button.setAttribute('aria-controls',popover.id);
    select.classList.add('composer-native-select');
    select.after(button); controls.push({select,button});
    button.onclick = () => {
      if (activeSelect===select) return closeOptions();
      closeOptions(); activeSelect=select; anchor=button;
      renderOptions(); setSurfaceVisible(popover,true,{key:id});
      button.setAttribute('aria-expanded','true'); positionOptions();
      popover.querySelector('[aria-selected="true"]')?.focus({preventScroll:true});
    };
    select.addEventListener('change',()=>queueMicrotask(sync));
    new MutationObserver(sync).observe(select,{childList:true,subtree:true,attributes:true});
  }
  popover.onkeydown = event => {
    if (event.key==='Escape') { const button=anchor; closeOptions(); button?.focus(); event.stopPropagation(); }
    if (!['ArrowDown','ArrowUp','ArrowRight','ArrowLeft','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const options=[...popover.querySelectorAll('[role="option"]:not(:disabled)')];
    const index=options.indexOf(document.activeElement);
    const next=event.key==='Home'?0:event.key==='End'?options.length-1:(index+(['ArrowDown','ArrowRight'].includes(event.key)?1:-1)+options.length)%options.length;
    options[next]?.focus();
  };
  document.addEventListener('pointerdown',event=>{
    if (!event.target.closest('.node-option-popover,.composer-option')) closeOptions();
  });
  const row=$('.composer-reference-row');
  $('#chooseReferenceBtn').innerHTML=icon('plus')+'<span>参考</span>';
  $('#chooseReferenceBtn').title='从资产库添加图片、视频或音频参考';
  row.insertAdjacentHTML('beforeend',`<button type="button" id="composerUpload" title="上传参考图片">${icon('upload')}<span>上传</span></button>`);
  $('#composerUpload').onclick=()=>$('#fileInput').click();
  function sync() {
    for (const {select,button} of controls) {
      const text=select.selectedOptions[0]?.textContent||'请选择';
      button.innerHTML=icon(glyphs[select.id])+`<span>${esc(text)}</span>`+icon('chevronDown');
      button.title=labels[select.id]+'：'+text;
      button.disabled=select.disabled;
    }
    if (activeSelect) {renderOptions();positionOptions();}
  }
  async function capture(node, video) {
    if (!video || video.readyState<2 || !video.videoWidth || video.seeking) throw new Error('请等待视频画面加载完成后截帧');
    if (video.videoWidth*video.videoHeight>64_000_000) throw new Error('视频尺寸过大，请先缩小后截帧');
    video.pause();
    const projectId=state.project?.id, time=video.currentTime;
    const picture=document.createElement('canvas'); picture.width=video.videoWidth; picture.height=video.videoHeight;
    picture.getContext('2d').drawImage(video,0,0);
    let blob;
    try { blob=await new Promise(resolve=>picture.toBlob(resolve,'image/png')); }
    catch { throw new Error('该视频源不允许截帧，请下载并上传到资产库后重试'); }
    if (!blob || blob.size>20*1024*1024) throw new Error('画面无法导出或图片超过 20 MB');
    const form=new FormData(); if(projectId)form.append('project_id',projectId); form.append('file',blob,`${node.title||'视频'}-${time.toFixed(2)}s.png`);
    const asset=await api.request('/api/assets',{method:'POST',body:form});
    api.upsertAsset(asset);
    if (state.project?.id===projectId && state.nodes.includes(node)) api.addLibraryAsset(asset,{position:{x:node.x+360,y:node.y}});
    api.toast('当前画面已加入生成历史');
  }
  async function action(name) {
    const node=api.selectedNode(); if (!node || busy) return;
    const card=$(`.node[data-node="${CSS.escape(node.id)}"]`), video=card?.querySelector('video');
    const asset=api.nodeAsset(node), task=state.nodeTasks[node.id];
    try {
      if (name==='play') { if(video.paused) await video.play(); else video.pause(); return; }
      if (name==='capture') { busy=true; toolbar.querySelector('[data-context="capture"]').disabled=true; await capture(node,video); }
      if (name==='download') {
        const url=node.type==='video'?api.videoPreviewUrl(node,task):asset?.url;
        if(url) {const a=document.createElement('a'); a.href=url; a.download=asset?.name||node.title; a.target='_blank'; a.rel='noopener'; a.click();}
      }
      if (name==='save') {busy=true;toolbar.querySelector('[data-context="save"]').disabled=true;await api.saveNode(node);}
      if (name==='image') api.generateNodeImage(node);
      if (name==='audio') api.generateNodeAudio(node);
      if (name==='reference') api.generateFromImage(asset || await api.prepareVideoReference(node),node);
      if (name==='pick') api.chooseNodeMedia(node);
      if (name==='upload') api.uploadNodeMedia(node);
      if (name==='polish') $('#polishPromptBtn')?.click();
      if (name==='script') api.openScriptWorkflow(node.id);
      if (name==='expand') api.expandScriptShots(node);
      if (name==='character') $('#detailCharacterName')?.focus({preventScroll:true});
    } catch(error) {api.toast(error.message);}
    finally {busy=false; toolbarKey='';refresh();}
  }
  toolbar.onclick=event=>{const target=event.target.closest('[data-context]');if(target) action(target.dataset.context);};
  const taskReady=node=>state.nodeTasks[node.id]?.status==='done'&&state.nodeTasks[node.id]?.video_url;
  function paint(node,visible) {
    videoControls.sync(node,visible);
    if (selectedId!==node?.id || !visible) {closeOptions(); selectedId=node?.id;}
    toolbar.classList.toggle('gesture-hidden',!!state.drag?.moved);
    if (!visible) {setSurfaceVisible(toolbar,false);return;}
    sync();
    const asset=api.nodeAsset(node), card=$(`.node[data-node="${CSS.escape(node.id)}"]`);
    const video=card?.querySelector('video');
    const actions=[];
    if(video) actions.push(['play','play','播放 / 暂停'],['capture','imageNode','截取当前帧']);
    if(['asset','prop'].includes(node.type)) {
      actions.push(['image','sparkle','AI 生图']);
      if(asset?.url) actions.push(['reference','video','作为视频参考']);
    }
    if(node.type==='video-asset'&&asset?.url || node.type==='video'&&taskReady(node)) actions.push(['reference','video','作为视频参考']);
    if(node.type==='audio') actions.push(['audio','audio','生成音频']);
    if(['asset','prop','audio','video-asset'].includes(node.type)) actions.push(['pick','library','更换素材'],['upload','upload','上传']);
    if(node.type==='prompt') actions.push(['polish','sparkle','润色']);
    if(node.type==='script') actions.push(['script','script','编辑分镜'],['expand','grid','展开分镜']);
    if(node.type==='character') actions.push(['character','character','编辑角色']);
    if(node.type==='video'&&taskReady(node)||asset?.url) {
      const saved=asset?asset.in_library:node.type==='video'&&state.nodeTasks[node.id]?.asset_save_status==='saved';
      actions.push(['save',saved?'libraryRemove':'library',saved?'移出资产库':'保存至资产库']);
    }
    if(video || asset?.url) actions.push(['download','download','下载']);
    const key=node.id+JSON.stringify(actions);
    if(key!==toolbarKey) {
      toolbarKey=key;
      toolbar.innerHTML=actions.map(([name,glyph,label])=>`<button type="button" data-context="${name}" title="${label}">${icon(glyph)}<span>${label}</span></button>`).join('');
    }
    setSurfaceVisible(toolbar,actions.length>0,{key:node.id});
    if(card && actions.length) {
      const bounds=canvas.getBoundingClientRect(),r=card.getBoundingClientRect();
      toolbar.style.transformOrigin='top left';
      toolbar.style.transform=`scale(${state.zoom})`;
      const desiredLeft=r.left-bounds.left+r.width/2-toolbar.offsetWidth*state.zoom/2;
      toolbar.style.left=Math.max(12,Math.min(canvas.clientWidth-toolbar.offsetWidth*state.zoom-12,desiredLeft))+'px';
      toolbar.style.top=Math.max(60,r.top-bounds.top-(toolbar.offsetHeight+10)*state.zoom)+'px';
    }
    positionOptions();
  }
  return {paint};
}
